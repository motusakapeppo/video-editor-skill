"""Public API surface: typed requests, read-only triage, EDL validation, doctor."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import api, auto, contract  # noqa: E402

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


def _plan_doc() -> dict:
    return {
        "contract_version": "2.2.0",
        "timeline": {
            "fps": 25.0,
            "duration_sec": 2.0,
            "aspect_ratio": "9:16",
            "resolution": [1080, 1920],
        },
        "sources": {"src_01": {"path": "a.mp4", "sha256": "a" * 64, "duration": 2.0}},
        "tracks": {
            "video": [
                {
                    "track_id": 0,
                    "role": "main",
                    "clips": [
                        {
                            "clip_id": "c1",
                            "source_id": "src_01",
                            "src_in": 0.0,
                            "src_out": 2.0,
                            "dst_in": 0.0,
                            "dst_out": 2.0,
                            "layer": 0,
                            "role": "main_talking_head",
                        }
                    ],
                }
            ],
            "audio": [],
        },
    }


def case_run_auto_forwarding() -> None:
    captured = {}
    sentinel = {"ok": True}
    original = auto.run

    def recorder(namespace):
        captured["namespace"] = namespace
        return sentinel

    auto.run = recorder
    try:
        request = api.AutoRequest(
            prompt="p",
            assets="a",
            out="o",
            workdir="w",
            fps=24.0,
            manifest="m",
            autonomous=True,
            crf=20,
            preset="slow",
        )
        result = api.run_auto(request)
    finally:
        auto.run = original

    namespace = captured.get("namespace")
    expected = {
        "prompt": "p",
        "assets": "a",
        "out": "o",
        "workdir": "w",
        "fps": 24.0,
        "manifest": "m",
        "autonomous": True,
        "crf": 20,
        "preset": "slow",
    }
    actual = vars(namespace) if namespace is not None else {}
    check(
        "run_auto nove attributi esatti",
        set(actual) == set(expected),
        sorted(actual),
    )
    check(
        "run_auto valori",
        all(actual.get(key) == value for key, value in expected.items()),
        actual,
    )
    check("run_auto ritorna l'oggetto del recorder", result is sentinel, result)


def case_run_auto_silent() -> None:
    original = auto.run
    auto.run = lambda namespace: {"ok": True}
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            api.run_auto(api.AutoRequest(prompt="p", assets="a", out="o"))
    finally:
        auto.run = original
    check("run_auto non stampa", buffer.getvalue() == "", repr(buffer.getvalue()[:60]))


def case_triage_empty(tmp: Path) -> None:
    empty = tmp / "empty"
    empty.mkdir()
    before = sorted(item.name for item in empty.iterdir())
    result = api.triage(empty, 1080, 1920)
    after = sorted(item.name for item in empty.iterdir())
    check("triage selected vuoto", result.get("selected") == [], result.get("selected"))
    check("triage rejected vuoto", result.get("rejected") == [], result.get("rejected"))
    check("triage total 0", result.get("total") == 0, result.get("total"))
    check(
        "triage role_source heuristic",
        result.get("role_source") == "heuristic",
        result.get("role_source"),
    )
    check("triage non scrive nella cartella", before == after, after)


def case_triage_validation(tmp: Path) -> None:
    try:
        api.triage(tmp, 0, 1920)
    except contract.VxError as exc:
        check("triage width 0 VxError", True, str(exc)[:60])
    except Exception as exc:  # noqa: BLE001
        check("triage width 0 VxError", False, type(exc).__name__)
    else:
        check("triage width 0 VxError", False, "nessuna eccezione")

    ghost = tmp / "ghost"
    try:
        api.triage(ghost, 1080, 1920)
    except ValueError as exc:
        check("triage dir inesistente ValueError", True, str(exc)[:60])
    except Exception as exc:  # noqa: BLE001
        check("triage dir inesistente ValueError", False, type(exc).__name__)
    else:
        check("triage dir inesistente ValueError", False, "nessuna eccezione")


def case_triage_still(tmp: Path) -> None:
    still_dir = tmp / "still"
    still_dir.mkdir()
    still = still_dir / "still.png"
    ok = _make(
        still,
        ["-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=1", "-frames:v", "1"],
    )
    if not ok:
        skip("triage still 1080x1920", "ffmpeg non disponibile")
        return

    result = api.triage(still_dir, 1080, 1920)
    selected = result.get("selected") or []
    check("triage still selezionata", len(selected) == 1, len(selected))
    entry = selected[0] if selected else {}
    check("triage still role b_roll", entry.get("role") == "b_roll", entry.get("role"))
    check(
        "triage still treatment ken_burns",
        entry.get("treatment") == "ken_burns_zoom_in",
        entry.get("treatment"),
    )


def case_validate_edl(tmp: Path) -> None:
    document = _plan_doc()
    good = api.validate_edl(document)
    check("validate_edl ok", good.get("ok") is True, good.get("errors"))
    check("validate_edl errors vuoto", good.get("errors") == [], good.get("errors"))
    check(
        "validate_edl summary",
        good.get("contract_version") == "2.2.0"
        and good.get("video_tracks") == 1
        and good.get("audio_tracks") == 0
        and good.get("duration_sec") == 2.0
        and good.get("resolution") == [1080, 1920],
        {
            key: good.get(key)
            for key in (
                "contract_version",
                "video_tracks",
                "audio_tracks",
                "duration_sec",
                "resolution",
            )
        },
    )

    broken = _plan_doc()
    broken["tracks"]["video"][0]["clips"][0]["src_out"] = 0.0
    bad = api.validate_edl(broken)
    check("validate_edl rotto", bad.get("ok") is False, bad.get("ok"))
    check("validate_edl errori non vuoti", bool(bad.get("errors")), bad.get("errors"))

    as_string = api.validate_edl(json.dumps(document))
    path = tmp / "plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    as_path = api.validate_edl(path)
    check(
        "validate_edl tre forme concordano",
        good.get("ok") == as_string.get("ok") == as_path.get("ok") is True,
        (good.get("ok"), as_string.get("ok"), as_path.get("ok")),
    )


def case_doctor() -> None:
    report = api.doctor()
    check(
        "doctor dict con verdict",
        isinstance(report, dict) and "verdict" in report,
        report.get("verdict") if isinstance(report, dict) else type(report).__name__,
    )


def case_read_only(tmp: Path) -> None:
    fixture = tmp / "fixture.txt"
    fixture.write_text("immutable\n", encoding="utf-8")
    before = _sha256(fixture)
    api.triage(tmp, 1080, 1920)
    api.validate_edl(_plan_doc())
    api.doctor()
    after = _sha256(fixture)
    check("fixture intatta", before == after, before[:12])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-api-") as raw:
        tmp = Path(raw)
        case_run_auto_forwarding()
        case_run_auto_silent()
        case_triage_empty(tmp)
        case_triage_validation(tmp)
        case_triage_still(tmp)
        case_validate_edl(tmp)
        case_doctor()
        case_read_only(tmp)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
