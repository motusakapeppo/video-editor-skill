"""Final master gate: loudness and true peak measured on the delivered file.

The multitrack EDL render has no loudness check of its own. ``audit_master``
measures the file with ``lufs_of`` and fails hard when the master drifts from
the declared delivery target. The EditPlan path keeps its 0.3 LUFS limit.

python tests/test_qc_master.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import qc  # noqa: E402
from vx.contract import QCFailure, VxError  # noqa: E402

PASS = 0
FAIL = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


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
    with tempfile.TemporaryDirectory(prefix="vx-master-") as raw:
        tmp = Path(raw)
        tone = tmp / "tone.mp4"
        silent = tmp / "silent.mp4"
        video_only = tmp / "video_only.mp4"

        ok_tone = _make(
            tone,
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25",
                "-t",
                "2",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
            ],
        )
        ok_silent = _make(
            silent,
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25",
                "-t",
                "2",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
            ],
        )
        ok_video = _make(
            video_only,
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25",
                "-t",
                "2",
                "-pix_fmt",
                "yuv420p",
            ],
        )
        if not (ok_tone and ok_silent and ok_video):
            print("  [SKIP] fixture: ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL")
            return 0

        before = {p.name: _sha256(p) for p in (tone, silent, video_only)}

        measured = qc.lufs_of(tone)
        measured_i = measured.get("input_i")
        measured_tp = measured.get("input_tp")
        check(
            "lufs_of misura il tono",
            measured_i is not None and measured_tp is not None,
            f"i={measured_i} tp={measured_tp}",
        )
        if measured_i is None or measured_tp is None:
            print(f"\n{PASS} PASS / {FAIL} FAIL")
            return 0 if FAIL == 0 else 1

        target_tp = max(measured_tp, -1.0)
        result = qc.audit_master(tone, target_lufs=measured_i, target_tp=target_tp)
        check("master conforme: ok True", result["ok"] is True, result["ok"])
        check(
            "master conforme: sei check, tutti pass",
            len(result["checks"]) == 6
            and all(c["status"] == "pass" for c in result["checks"]),
            [c["status"] for c in result["checks"]],
        )
        check(
            "master conforme: forma del risultato",
            set(result)
            == {"ok", "measured", "target", "tolerance_lu", "checks", "failures"}
            and result["failures"] == []
            and result["tolerance_lu"] == 1.5,
            sorted(result),
        )

        try:
            qc.audit_master(tone, target_lufs=measured_i + 2.0, target_tp=target_tp)
        except QCFailure as exc:
            payload = exc.result
            lufs = next(c for c in payload["checks"] if c["code"] == "master_lufs")
            check(
                "target +2.0: QCFailure con master_lufs",
                payload["ok"] is False and "master_lufs" in payload["failures"],
                payload["failures"],
            )
            check(
                "target +2.0: il detail stampa il delta reale",
                "2.0" in lufs["detail"],
                lufs["detail"],
            )
        else:
            check("target +2.0: QCFailure con master_lufs", False, "nessuna eccezione")
            check(
                "target +2.0: il detail stampa il delta reale",
                False,
                "nessuna eccezione",
            )

        try:
            qc.audit_master(video_only, target_lufs=-14.0, target_tp=-1.0)
        except QCFailure as exc:
            check(
                "video senza audio: master_audio nei failures",
                "master_audio" in exc.result["failures"],
                exc.result["failures"],
            )
        else:
            check(
                "video senza audio: master_audio nei failures",
                False,
                "nessuna eccezione",
            )

        ghost = tmp / "ghost.mp4"
        try:
            qc.audit_master(ghost, target_lufs=-14.0, target_tp=-1.0)
        except QCFailure as exc:
            check(
                "path inesistente: master_exists nei failures",
                "master_exists" in exc.result["failures"],
                exc.result["failures"],
            )
        else:
            check(
                "path inesistente: master_exists nei failures",
                False,
                "nessuna eccezione",
            )

        for bad in (0, 9):
            try:
                qc.audit_master(
                    tone,
                    target_lufs=measured_i,
                    target_tp=target_tp,
                    tolerance_lu=bad,
                )
            except VxError as exc:
                check(
                    f"tolerance_lu={bad}: VxError",
                    type(exc) is VxError,
                    type(exc).__name__,
                )
            except Exception as exc:  # noqa: BLE001
                check(f"tolerance_lu={bad}: VxError", False, type(exc).__name__)
            else:
                check(f"tolerance_lu={bad}: VxError", False, "nessuna eccezione")

        check(
            "check_render conserva lufs_delta 0.3",
            qc.QC_DEFAULTS["lufs_delta"] == 0.3,
            qc.QC_DEFAULTS["lufs_delta"],
        )
        check(
            "master_lufs_delta 1.5",
            qc.QC_DEFAULTS["master_lufs_delta"] == 1.5,
            qc.QC_DEFAULTS["master_lufs_delta"],
        )

        after = {p.name: _sha256(p) for p in (tone, silent, video_only)}
        check("sorgenti intatte", before == after, sorted(before))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
