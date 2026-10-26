"""Ingest orchestrator: N sources, one stage-00 result per source_id."""

from __future__ import annotations

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
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def ffmpeg(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )


def _clean(path: Path) -> bool:
    return (
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x180:rate=25:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ]
        ).returncode
        == 0
    )


def _vfr(path: Path) -> bool:
    return (
        ffmpeg(
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
                str(path),
            ]
        ).returncode
        == 0
    )


def _hdr(path: Path) -> bool:
    return (
        ffmpeg(
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
                str(path),
            ]
        ).returncode
        == 0
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-multi-ingest-") as raw:
        tmp = Path(raw)
        stage = tmp / "stage"

        clean, vfr, hdr = tmp / "clean.mp4", tmp / "vfr.mp4", tmp / "hdr.mp4"
        if not (_clean(clean) and _vfr(vfr) and _hdr(hdr)):
            skip("fixture", "ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 0

        try:
            results = ingest.ingest_sources(
                {"src_clean": clean, "src_vfr": vfr, "src_hdr": hdr}, stage
            )
        except contract.MissingDependency as exc:
            results = ingest.ingest_sources({"src_clean": clean, "src_vfr": vfr}, stage)
            skip("HDR staging", str(exc))
        except Exception as exc:  # noqa: BLE001
            check("ingest_sources completa", False, f"{type(exc).__name__}: {exc}")
            print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
            return 1

        check(
            "tre source_id nella mappa",
            list(results) == ["src_clean", "src_vfr", "src_hdr"]
            or list(results) == ["src_clean", "src_vfr"],
            list(results),
        )
        check(
            "clean non stagiato",
            results["src_clean"].is_staged is False
            and results["src_clean"].staged_path == str(clean.resolve()),
            results["src_clean"].staged_path,
        )
        check(
            "VFR stagiato",
            results["src_vfr"].is_staged is True
            and results["src_vfr"].was_vfr is True
            and Path(results["src_vfr"].staged_path).is_file(),
            results["src_vfr"].staged_path,
        )
        check(
            "nome VFR legato al source_id",
            "src_vfr" in Path(results["src_vfr"].staged_path).name,
            Path(results["src_vfr"].staged_path).name,
        )
        if "src_hdr" in results:
            check(
                "HDR stagiato",
                results["src_hdr"].is_staged is True
                and results["src_hdr"].was_hdr is True
                and Path(results["src_hdr"].staged_path).is_file(),
                results["src_hdr"].staged_path,
            )
            check(
                "nome HDR legato al source_id",
                "src_hdr" in Path(results["src_hdr"].staged_path).name,
                Path(results["src_hdr"].staged_path).name,
            )
            hdr_probe = probe.probe(results["src_hdr"].staged_path)
            transfer = (hdr_probe.color or {}).get("transfer", "")
            check(
                "HDR sceso a BT.709",
                transfer in ("bt709", ""),
                hdr_probe.color,
            )

        for source_id, result in results.items():
            check(
                f"probe {source_id}",
                probe.probe(result.staged_path) is not None,
                result.staged_path,
            )

        first, second = tmp / "a" / "clip.mp4", tmp / "b" / "clip.mp4"
        first.parent.mkdir()
        second.parent.mkdir()
        if not (_vfr(first) and _vfr(second)):
            skip("collisione stem", "VFR generator unavailable")
        else:
            collided = ingest.ingest_sources(
                {"src_a": first, "src_b": second}, tmp / "stage_collision"
            )
            check(
                "stem uguale, stage distinti",
                collided["src_a"].staged_path != collided["src_b"].staged_path,
                (
                    collided["src_a"].staged_path,
                    collided["src_b"].staged_path,
                ),
            )
            check(
                "entrambi gli stage esistono",
                Path(collided["src_a"].staged_path).is_file()
                and Path(collided["src_b"].staged_path).is_file(),
            )

        try:
            ingest.ingest_sources({}, stage)
        except ingest.IngestError:
            check("mappa vuota rifiutata", True)
        except Exception as exc:  # noqa: BLE001
            check("mappa vuota rifiutata", False, f"{type(exc).__name__}: {exc}")
        else:
            check("mappa vuota rifiutata", False, "accettata")

        try:
            ingest.ingest_sources({"": clean}, stage)
        except ingest.IngestError:
            check("source_id vuoto rifiutato", True)
        except Exception as exc:  # noqa: BLE001
            check("source_id vuoto rifiutato", False, f"{type(exc).__name__}: {exc}")
        else:
            check("source_id vuoto rifiutato", False, "accettato")

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
