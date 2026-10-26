"""Cross-platform contract: encodings, separators and optional dependencies.

The suite has to behave the same on Windows, Linux and macOS. The bugs that
actually bite are boring and reproducible:

* a JSON file written with a UTF-16 BOM by a Windows editor;
* a child process whose output is decoded with the platform default codec;
* a path with spaces and accents crossing the CLI boundary;
* an optional dependency missing and a module that raises instead of degrading.

This file pins those behaviours. It never needs a GPU and never needs network.

Run:
    python tests/test_cross_platform.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from vx import accel, cli, subs, vad, winpath  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:64} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"SKIP {label:64} {reason}")


def _write_bytes(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    return path


def _utf16(prefix: bytes, obj: dict, endian: str) -> bytes:
    return prefix + json.dumps(obj, ensure_ascii=False).encode(f"utf-16-{endian}")


def check_bom_json(tmp: Path) -> None:
    obj = {"format": "9:16", "nota": "città perché", "n": 7}
    cases = [
        ("UTF-16 LE BOM", _utf16(b"\xff\xfe", obj, "le")),
        ("UTF-16 BE BOM", _utf16(b"\xfe\xff", obj, "be")),
        (
            "UTF-8 BOM",
            b"\xef\xbb\xbf" + json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        ),
        ("UTF-8 senza BOM", json.dumps(obj, ensure_ascii=False).encode("utf-8")),
    ]
    for index, (label, raw) in enumerate(cases):
        path = _write_bytes(tmp / f"bom_{index}.json", raw)
        try:
            loaded = cli._load_json(path)
        except Exception as exc:  # noqa: BLE001 - the failure is the result
            check(f"_load_json: {label}", False, f"{type(exc).__name__}: {exc}")
            continue
        check(
            f"_load_json: {label}",
            loaded == obj,
            loaded.get("nota", ""),
        )

    bad = _write_bytes(tmp / "bad.json", b"\xff\xfe\x00")
    try:
        cli._load_json(bad)
        check("_load_json: UTF-16 troncato -> VxError", False, "nessuna eccezione")
    except cli.VxError as exc:
        check("_load_json: UTF-16 troncato -> VxError", True, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        check(
            "_load_json: UTF-16 troncato -> VxError",
            False,
            f"eccezione sbagliata: {type(exc).__name__}",
        )


def check_child_decoding(tmp: Path) -> None:
    import run_all

    samples = {
        "UTF-8": "perché\n".encode("utf-8"),
        "UTF-8 BOM": b"\xef\xbb\xbf" + "perché\n".encode("utf-8"),
        "UTF-16 LE": b"\xff\xfe" + "perché\n".encode("utf-16-le"),
        "UTF-16 BE": b"\xfe\xff" + "perché\n".encode("utf-16-be"),
    }
    for label, raw in samples.items():
        text = run_all._decode_output(raw)
        check(f"_decode_output: {label}", text.strip() == "perché", text.strip())

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", "print('perché')"],
        capture_output=True,
        env=env,
    )
    text = run_all._decode_output(proc.stdout or b"")
    check(
        "processo figlio: accento round-trip",
        proc.returncode == 0 and text.strip() == "perché",
        text.strip(),
    )


def check_paths(tmp: Path) -> None:
    folder = tmp / "città perché" / "con spazi"
    folder.mkdir(parents=True, exist_ok=True)
    brief = folder / "brief.json"
    brief.write_text(
        json.dumps({"format": "9:16", "hook": "none"}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        loaded = cli._load_json(brief)
    except Exception as exc:  # noqa: BLE001
        check("percorso non-ASCII con spazi", False, f"{type(exc).__name__}: {exc}")
    else:
        check(
            "percorso non-ASCII con spazi",
            loaded.get("format") == "9:16" and brief.is_file(),
            str(brief.name),
        )

    value = winpath.ass_filter_path(Path("assets") / "clip con spazi.ass")
    check(
        "winpath: filtro quotato con spazi",
        value.startswith("'") and value.endswith("'") and "clip con spazi.ass" in value,
        value,
    )
    check(
        "winpath: nessun backslash non escapato",
        "\\:" in value or "\\" not in value,
        value,
    )

    stem = winpath.safe_stem("clip: con* caratteri? illegali.mp4")
    check(
        "winpath: stem portabile",
        bool(stem) and all(ch not in stem for ch in ': *?"<>|/\\'),
        stem,
    )


def check_optional_dependencies() -> None:
    missing = Path(tempfile.gettempdir()) / "vx-does-not-exist-9f3a.mp4"
    tr = subs.transcribe(str(missing))
    check(
        "subs.transcribe: file assente -> skipped_reason",
        bool(tr.skipped_reason) and tr.words == [],
        tr.skipped_reason,
    )

    result = vad.detect(str(missing))
    check(
        "vad.detect: file assente -> degrada",
        result.available is False and bool(result.reason),
        result.reason,
    )

    target = accel.whisper_target(preferred="cpu")
    check(
        "accel: fallback CPU esplicito",
        target.get("device") == "cpu" and bool(target.get("compute_type")),
        target,
    )
    choice = subs._device_choice("cpu", "int8")
    check(
        "subs: device choice CPU",
        choice.get("device") == "cpu" and choice.get("compute_type") == "int8",
        choice,
    )


def check_ffmpeg(tmp: Path) -> None:
    try:
        version = subprocess.run(
            [FFMPEG, "-version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        skip("ffmpeg disponibile", str(exc))
        return
    if version.returncode != 0:
        skip("ffmpeg disponibile", version.stderr[-200:])
        return
    check("ffmpeg disponibile", True, version.stdout.splitlines()[0][:40])

    clip = tmp / "smoke.mp4"
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=25:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    check(
        "ffmpeg: encode di prova",
        proc.returncode == 0 and clip.is_file() and clip.stat().st_size > 0,
        proc.stderr[-200:],
    )


def check_ci_yaml() -> None:
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        skip("CI: workflow presente", "assente")
        return
    try:
        import yaml  # type: ignore
    except Exception as exc:  # noqa: BLE001
        skip("CI: YAML leggibile", f"pyyaml assente: {exc}")
        return
    try:
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("CI: YAML leggibile", False, f"{type(exc).__name__}: {exc}")
        return
    check("CI: YAML leggibile", isinstance(data, dict), type(data).__name__)
    jobs = (data or {}).get("jobs") or {}
    check("CI: almeno un job", bool(jobs), ",".join(jobs))
    matrix: Any = {}
    for job in jobs.values():
        matrix = ((job or {}).get("strategy") or {}).get("matrix") or {}
        if matrix:
            break
    systems = list(matrix.get("os") or [])
    pythons = list(matrix.get("python") or matrix.get("python-version") or [])
    check("CI: tre sistemi operativi", len(systems) == 3, ",".join(map(str, systems)))
    check("CI: tre versioni di Python", len(pythons) == 3, ",".join(map(str, pythons)))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-xplat-") as raw:
        tmp = Path(raw)
        check_bom_json(tmp)
        check_child_decoding(tmp)
        check_paths(tmp)
        check_optional_dependencies()
        check_ffmpeg(tmp)
    check_ci_yaml()

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
