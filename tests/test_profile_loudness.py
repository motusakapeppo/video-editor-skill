"""F26-T02 -- the loudness target belongs to the profile, and a measurement proves it.

    python tests/test_profile_loudness.py

Four profiles cover the four declared targets (-14, -16, -23, -24). The same
short source is normalised to each with a two-pass loudnorm; the produced file
is then measured fresh, and the measured integrated loudness must land within
0.5 LU of the profile target and the measured true peak at or under the
profile's ceiling. The report's output numbers are compared against that
independent measurement, so a requested number copied into the report cannot
pass. Refusals (no audio, unparseable measurement) and the profile-less
default (-14) are covered without rendering a full pipeline.

The source sits ~-30 LUFS, so every target needs a real, non-zero gain.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, profile_master, profiles, qc  # noqa: E402
from vx.edl3 import EDLError  # noqa: E402

PASS = 0
FAIL = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"
TOLERANCE_LU = 0.5

CASES = (
    ("reel", -14.0, -1.0),
    ("documentario", -16.0, -1.5),
    ("spot", -23.0, -1.0),
    ("film", -24.0, -2.0),
)

REPORT_KEYS = (
    "input_lufs",
    "input_true_peak_db",
    "applied_gain_db",
    "output_lufs",
    "output_true_peak_db",
    "tolerance_lu",
    "within_tolerance",
    "trim_passes",
)


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def _make(path: Path, args: list) -> bool:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def _source(tmp: Path) -> Path:
    path = tmp / "source.mp4"
    _make(
        path,
        [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
            "-f", "lavfi", "-i", "color=c=black:s=320x568:r=25",
            "-t", "2", "-shortest", "-pix_fmt", "yuv420p",
            "-af", "volume=-8dB", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
        ],
    )
    return path


def _no_audio(tmp: Path) -> Path:
    path = tmp / "silent_video.mp4"
    _make(
        path,
        [
            "-f", "lavfi", "-i", "color=c=black:s=320x568:r=25",
            "-t", "2", "-pix_fmt", "yuv420p",
        ],
    )
    return path


def _code(fn) -> str:
    try:
        fn()
    except EDLError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - a wrong type is a failure
        return f"__WRONG_TYPE__{type(exc).__name__}: {exc}"
    return ""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-profile-loudness-") as raw:
        tmp = Path(raw)
        source = _source(tmp)
        silent = _no_audio(tmp)
        if not (source.is_file() and silent.is_file()):
            print("  [SKIP] fixture: ffmpeg non disponibile")
            print(f"\n{PASS} PASS / {FAIL} FAIL")
            return 0

        work = tmp / "work"
        measured_source = qc.lufs_of(source)
        source_lufs = float(measured_source["input_i"])

        print("a. every profile target is hit, measured on the produced file")
        reports = {}
        for profile_id, want_lufs, want_tp in CASES:
            target = tmp / f"{profile_id}.mp4"
            report = profile_master.master(source, profile_id, target, work)
            reports[profile_id] = (report, target)

            fresh = qc.lufs_of(target)
            got_lufs = float(fresh["input_i"])
            got_tp = float(fresh["input_tp"])
            delta = abs(got_lufs - want_lufs)
            print(
                f"    {profile_id:14} target {want_lufs:6.1f}/{want_tp:5.1f}  "
                f"measured {got_lufs:7.2f}/{got_tp:6.2f}  delta {delta:.2f} LU"
            )
            check(
                f"a. {profile_id} is within {TOLERANCE_LU} LU of {want_lufs}",
                delta <= TOLERANCE_LU,
                f"{got_lufs:.3f} LUFS",
            )
            check(
                f"a. {profile_id} true peak {got_tp:.2f} <= {want_tp}",
                got_tp <= want_tp,
                f"{got_tp:.3f} dBTP",
            )
            check(
                f"a. {profile_id} report names its own target",
                report["target"] == {"lufs": want_lufs, "true_peak_db": want_tp},
                report["target"],
            )

        print("\nb. the report carries measurements, not the request")
        first = reports["reel"][0]
        check(
            "b1. every measured key is present",
            all(key in first for key in REPORT_KEYS),
            sorted(first),
        )
        check(
            "b0. the report declares its tolerance and whether it met it",
            first["tolerance_lu"] == TOLERANCE_LU and first["within_tolerance"] is True,
            f"{first['tolerance_lu']} LU, met={first['within_tolerance']}",
        )
        check(
            "b0. every trim it ran is listed and moved the file",
            all(
                isinstance(row, dict) and row.get("pass", 0) >= 1
                for row in first["trim_passes"]
            ),
            f"{len(first['trim_passes'])} trim(s)",
        )

        fresh_first = qc.lufs_of(reports["reel"][1])
        check(
            "b2. output_lufs equals the fresh measurement of the file",
            abs(first["output_lufs"] - float(fresh_first["input_i"])) < 0.001,
            f"report={first['output_lufs']} measured={fresh_first['input_i']}",
        )
        check(
            "b3. output_true_peak_db equals the fresh measurement",
            abs(first["output_true_peak_db"] - float(fresh_first["input_tp"])) < 0.001,
            f"report={first['output_true_peak_db']} measured={fresh_first['input_tp']}",
        )
        check(
            "b4. input_lufs equals the fresh measurement of the source",
            abs(first["input_lufs"] - source_lufs) < 0.001,
            f"report={first['input_lufs']} measured={source_lufs}",
        )
        check(
            "b5. the applied gain is the difference of two measurements",
            abs(
                first["applied_gain_db"]
                - (first["output_lufs"] - first["input_lufs"])
            )
            < 0.002,
            first["applied_gain_db"],
        )
        check(
            "b6. the output is not the requested number by construction",
            any(
                abs(rep["output_lufs"] - rep["target"]["lufs"]) > 0.0
                for rep, _ in reports.values()
            ),
            {pid: rep["output_lufs"] for pid, (rep, _) in reports.items()},
        )
        check(
            "b7. every report names its profile",
            all(rep["profile"] == pid for pid, (rep, _) in reports.items()),
            [rep["profile"] for rep, _ in reports.values()],
        )

        print("\nc. target_for reads the same pair from an id and a Profile")
        pairs = {
            name: profile_master.target_for(name)
            for name in profiles.names()
        }
        check(
            "c1. string id and Profile agree on every declared row",
            all(
                pairs[name] == profile_master.target_for(profiles.resolve(name))
                for name in profiles.names()
            ),
            pairs,
        )
        check(
            "c2. reel is the -14 / -1 pair",
            pairs["reel"] == (-14.0, -1.0),
            pairs["reel"],
        )
        check(
            "c3. the four case targets are four distinct pairs",
            len({pairs[pid] for pid, _, _ in CASES}) == 4,
            [pairs[pid] for pid, _, _ in CASES],
        )

        print("\nd. refusals")
        check(
            "d1. a video with no audio stream is refused LOUDNESS_NO_AUDIO",
            _code(lambda: profile_master.master(silent, "reel", tmp / "x.mp4", work))
            == profile_master.LOUDNESS_NO_AUDIO,
            _code(lambda: profile_master.master(silent, "reel", tmp / "x.mp4", work)),
        )
        saved = profile_master.qc.lufs_of
        profile_master.qc.lufs_of = lambda path: {}
        try:
            unmeasured = _code(
                lambda: profile_master.master(source, "reel", tmp / "y.mp4", work)
            )
        finally:
            profile_master.qc.lufs_of = saved
        check(
            "d2. an unparseable loudnorm block is refused LOUDNESS_UNMEASURED",
            unmeasured == profile_master.LOUDNESS_UNMEASURED,
            unmeasured,
        )

        print("\ne. a document without profile_ref keeps -14 / -1")
        default_pair = auto.loudness_target({})
        check(
            "e1. the resolver returns the module constants",
            default_pair == (auto.TARGET_LUFS, auto.TARGET_TP),
            default_pair,
        )
        check("e2. that pair is -14 LUFS", default_pair[0] == -14.0, default_pair[0])
        check("e3. and -1 dBTP", default_pair[1] == -1.0, default_pair[1])
        check(
            "e4. a declared profile changes the pair",
            auto.loudness_target({"profile_ref": "spot"}) == (-23.0, -1.0),
            auto.loudness_target({"profile_ref": "spot"}),
        )

        print("\nf. the source is far enough from every target to need real gain")
        check(
            "f1. the source sits at least 5 LU below the loudest target",
            all(source_lufs < want - 5.0 for _, want, _ in CASES),
            f"source={source_lufs:.2f} LUFS",
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
