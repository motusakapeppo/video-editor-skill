"""QC confronta la misura col target ESEGUITO, non con una ri-derivazione.

Un render che dichiara loudnorm deve provare quale target ha usato. Se il
target eseguito manca, il cancello fallisce: non e' uno skip.

python tests/test_qc_loudness.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import qc  # noqa: E402
from vx.contract import EditPlan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def loudnorm(enabled: bool = True, params: dict | None = None) -> dict:
    return {
        "name": "loudnorm",
        "kind": "technical",
        "enabled": enabled,
        "params": (
            params if params is not None else {"I": -16.0, "TP": -1.0, "LRA": 11.0}
        ),
        "reason": {"finding_id": "audio.loudness.off", "explain": "test"},
    }


def plan_with(loudness: dict, treatments: list) -> EditPlan:
    return EditPlan(
        output={"aspect": "9:16", "loudness": loudness},
        treatments=list(treatments),
        cuts=[{"in": 0.0, "out": 10.0}],
        render={"precision": "full"},
    )


def run_qc(
    plan: EditPlan,
    measured_i: float = -14.0,
    measured_tp: float = -1.0,
    executed: dict | None = None,
    media_has_audio: bool = True,
):
    stubbed = (
        "probe",
        "lufs_of",
        "frame_count",
        "_streams",
        "_black_edges",
        "_nan_timestamps",
        "_faststart",
    )
    saved = {name: getattr(qc, name) for name in stubbed}
    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.write(b"\x00" * 16)
    handle.close()
    try:
        qc.probe = lambda path: SimpleNamespace(
            width=1080,
            height=1920,
            fps=30.0,
            duration=10.0,
            has_audio=True,
            audio_codec="aac",
            video_codec="h264",
            nb_frames=300,
        )
        qc.lufs_of = lambda path: {"input_i": measured_i, "input_tp": measured_tp}
        qc.frame_count = lambda path: 300
        qc._streams = lambda path: [
            {"codec_type": "video", "start_time": "0.0"},
            {"codec_type": "audio", "start_time": "0.0"},
        ]
        qc._black_edges = lambda path, duration: (0, "none")
        qc._nan_timestamps = lambda path: (True, "0 NaN timestamp(s)")
        qc._faststart = lambda path: (True, "moov@0 mdat@100")
        media = {
            "fps": 30.0,
            "duration": 10.0,
            "width": 1080,
            "height": 1920,
        }
        if media_has_audio:
            media["has_audio"] = True
        return qc.check_render(plan, media, handle.name, executed_target=executed)
    finally:
        for name, value in saved.items():
            setattr(qc, name, value)
        try:
            Path(handle.name).unlink()
        except OSError:
            pass


def by_code(result, code: str) -> dict:
    return next(c for c in result.checks if c["code"] == code)


def main() -> int:
    apple = {"i": -16.0, "tp": -1.0, "lra": 11.0}
    lower = plan_with(apple, [loudnorm()])

    result = run_qc(lower, measured_i=-14.0, measured_tp=-1.0, executed=apple)
    lufs = by_code(result, "lufs_delta")
    check(
        "target eseguito -16, misurato -14: delta 2.0, fallisce",
        lufs["pass"] is False and lufs["value"] == 2.0 and lufs["limit"] == 0.3,
        f"pass={lufs['pass']} value={lufs['value']}",
    )
    check(
        "il target eseguito e' registrato",
        by_code(result, "loudness_target_recorded")["pass"] is True,
        str(result.metrics.get("loudness_target")),
    )

    broadcast = {"i": -23.0, "tp": -2.0, "lra": 11.0}
    result = run_qc(lower, measured_i=-16.0, measured_tp=-1.0, executed=broadcast)
    lufs = by_code(result, "lufs_delta")
    check(
        "il target eseguito vince sul piano: delta 7.0",
        lufs["value"] == 7.0 and lufs["pass"] is False,
        f"value={lufs['value']} detail={lufs['detail']}",
    )

    result = run_qc(lower, executed=None)
    recorded = by_code(result, "loudness_target_recorded")
    lufs = by_code(result, "lufs_delta")
    check(
        "loudnorm attivo senza target eseguito: fallisce, non skip",
        recorded["pass"] is False
        and recorded["status"] == "checked"
        and lufs["pass"] is False,
        f"recorded={recorded['pass']} lufs={lufs['pass']}",
    )

    ungated = plan_with(apple, [])
    result = run_qc(ungated)
    lufs = by_code(result, "lufs_delta")
    peak = by_code(result, "true_peak")
    check(
        "senza loudnorm: lufs_delta skipped e verde",
        lufs["status"] == "skipped"
        and lufs["pass"] is True
        and lufs["detail"] == "SKIPPED: no loudnorm treatment (no finding)",
        f"status={lufs['status']} pass={lufs['pass']}",
    )
    check(
        "senza loudnorm: true_peak skipped",
        peak["status"] == "skipped" and peak["pass"] is True,
        f"status={peak['status']}",
    )
    check(
        "senza loudnorm: metrics loudness_gated False",
        result.metrics.get("loudness_gated") is False,
        str(result.metrics.get("loudness_gated")),
    )

    result = run_qc(lower, executed=apple, media_has_audio=False)
    has = by_code(result, "has_audio")
    check(
        "has_audio sconosciuto: audio atteso, nessun default silenzioso",
        has["limit"] is True and has["pass"] is True,
        f"limit={has['limit']} pass={has['pass']}",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
