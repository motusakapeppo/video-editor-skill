"""The approved-delivery gate must skip honestly, never fake a pass.

python tests/test_delivery_gate_optional.py

The private reference video is not part of the repository. On a machine without
it the gate has to say ``skipped`` and leave ``renderer_delta.json`` untouched;
a configured-but-wrong delivery has to fail. This test proves both, and proves
that no absolute path or hash is baked into the prover.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PROVE = SCRIPTS / "vx" / "prove.py"
REGISTRY = SCRIPTS / "vx" / "registry.py"
MEASURE = ROOT / "tests" / "measure_corpus.py"
FIXTURE = ROOT / "tests" / "fixtures" / "renderer_delta.json"

SKIPPED_DETAIL = (
    "SKIPPED: approved delivery not configured (VX_APPROVED_DELIVERY unset) — declared"
)

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _env(**overrides: str | None) -> dict:
    env = dict(os.environ)
    env.pop("VX_APPROVED_DELIVERY", None)
    env.pop("VX_APPROVED_SHA256", None)
    env["PYTHONPATH"] = str(SCRIPTS)
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _run(script: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
    )


def _run_module(module: str, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
    )


def _gate(env: dict) -> tuple[subprocess.CompletedProcess, dict | None]:
    proc = _run_module("vx.prove", ["--delivery-gate"], env)
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "status" in candidate:
            return proc, candidate
    return proc, None


def main() -> int:
    if not FIXTURE.is_file():
        print(f"fixture mancante: {FIXTURE}")
        return 1
    before = _sha256(FIXTURE)

    print("delivery non configurato:")
    proc, result = _gate(_env())
    check("gate esce 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("gate stampa SKIPPED", "SKIPPED" in proc.stdout, proc.stdout.strip()[:60])
    check(
        "gate status == skipped", (result or {}).get("status") == "skipped", str(result)
    )
    check(
        "gate ok == True",
        (result or {}).get("ok") is True,
        str((result or {}).get("ok")),
    )
    check(
        "gate detail dichiarato",
        (result or {}).get("detail") == SKIPPED_DETAIL,
        str((result or {}).get("detail")),
    )
    check(
        "gate path == None",
        (result or {}).get("path") is None,
        str((result or {}).get("path")),
    )

    proc = _run_module("vx.prove", ["--selftest"], _env())
    check("selftest esce 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("selftest stampa SKIPPED", "SKIPPED" in proc.stdout, "")
    check(
        "selftest non dichiara verificato",
        "verified" not in proc.stdout.lower(),
        "",
    )

    proc = _run(MEASURE, [], _env())
    check("measure_corpus esce 0", proc.returncode == 0, f"rc={proc.returncode}")
    check(
        "measure_corpus stampa SKIPPED",
        "SKIPPED: approved delivery not configured" in proc.stdout,
        "",
    )
    check(
        "renderer_delta.json invariato",
        _sha256(FIXTURE) == before,
        f"{before[:12]}...",
    )

    print("delivery configurato ma errato:")
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    temp_path = Path(handle.name)
    try:
        handle.write(b"vx approved delivery probe\n")
        handle.close()
        real_sha = _sha256(temp_path)

        proc, result = _gate(
            _env(VX_APPROVED_DELIVERY=str(temp_path), VX_APPROVED_SHA256="deadbeef")
        )
        check(
            "hash errato: gate esce != 0", proc.returncode != 0, f"rc={proc.returncode}"
        )
        check(
            "hash errato: status == failed",
            (result or {}).get("status") == "failed",
            str((result or {}).get("status")),
        )
        check(
            "hash errato: non è skipped",
            (result or {}).get("status") != "skipped",
            "",
        )

        proc, result = _gate(
            _env(VX_APPROVED_DELIVERY=str(temp_path), VX_APPROVED_SHA256=real_sha)
        )
        check(
            "hash corretto: gate esce 0", proc.returncode == 0, f"rc={proc.returncode}"
        )
        check(
            "hash corretto: status == verified",
            (result or {}).get("status") == "verified",
            str((result or {}).get("status")),
        )

        missing = temp_path.with_name(temp_path.name + ".missing")
        proc, result = _gate(
            _env(VX_APPROVED_DELIVERY=str(missing), VX_APPROVED_SHA256=real_sha)
        )
        check(
            "path assente: gate esce != 0",
            proc.returncode != 0,
            f"rc={proc.returncode}",
        )
        check(
            "path assente: status == failed",
            (result or {}).get("status") == "failed",
            str((result or {}).get("status")),
        )
    finally:
        temp_path.unlink(missing_ok=True)

    print("nessun percorso privato nel codice:")
    for source in (PROVE, REGISTRY):
        text = source.read_text(encoding="utf-8")
        check(f"{source.name}: nessun D:\\", "D:\\" not in text, "")
        check(f"{source.name}: nessun C:\\Users", "C:\\Users" not in text, "")
        check(
            f"{source.name}: nessuna unità assoluta",
            re.search(r"\b[A-Za-z]:\\", text) is None,
            "",
        )

    check(
        "renderer_delta.json ancora invariato",
        _sha256(FIXTURE) == before,
        f"{before[:12]}...",
    )

    print(f"{'OK' if FAIL == 0 else 'FALLITO'} — {PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
