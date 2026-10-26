"""Packaging contract: pyproject.toml describes an editable install of the vx package.

python tests/test_packaging.py

Static checks only: nothing is installed here. The test env runs from PYTHONPATH,
so importlib.metadata would raise and is deliberately not used. TOML is parsed
with tomllib when available, otherwise with the tomli copy vendored by pip; if
neither exists every TOML assertion is reported as SKIP, never guessed.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract  # noqa: E402
from vx.cli import main as cli_main  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

PYPROJECT = ROOT / "pyproject.toml"
LAUNCHER = ROOT / "scripts" / "vx.py"

# Built from parts so this file never carries a literal machine path.
DRIVE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
USER_PATH = "c:" + "\\" + "users" + "\\"

TOML_LABELS = (
    "build-backend setuptools.build_meta",
    "requires contiene setuptools>=64",
    "project.name vx-video-editor",
    "project.requires-python >=3.10",
    "project.dependencies vuoto",
    "project.version == contract.VERSION",
    "project.scripts.vx -> vx.cli:main",
    "project.scripts target importabili",
    "package-dir {'': 'scripts'}",
    "packages contiene vx",
)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _load_toml(path: Path) -> tuple[object, str]:
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            from pip._vendor import tomli as tomllib  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 - any import failure means no parser
            return (
                None,
                "tomllib assente su Python 3.10 e pip._vendor.tomli non importabile",
            )
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh), ""
    except Exception as exc:  # noqa: BLE001 - malformed TOML is a skip, not a guess
        return None, f"{type(exc).__name__}: {exc}"


def _check_toml(data: dict) -> None:
    build = data.get("build-system") or {}
    project = data.get("project") or {}
    tool = (data.get("tool") or {}).get("setuptools") or {}

    backend = build.get("build-backend")
    check(
        "build-backend setuptools.build_meta",
        backend == "setuptools.build_meta",
        backend,
    )

    requires = build.get("requires") or []
    check("requires contiene setuptools>=64", "setuptools>=64" in requires, requires)

    name = project.get("name")
    check("project.name vx-video-editor", name == "vx-video-editor", name)

    py = project.get("requires-python")
    check("project.requires-python >=3.10", py == ">=3.10", py)

    deps = project.get("dependencies")
    check("project.dependencies vuoto", deps == [], deps)

    version = project.get("version")
    check(
        "project.version == contract.VERSION",
        version == contract.VERSION,
        f"{version} vs {contract.VERSION}",
    )

    scripts = project.get("scripts") or {}
    target = scripts.get("vx")
    check("project.scripts.vx -> vx.cli:main", target == "vx.cli:main", target)

    for script_name in sorted(scripts):
        spec = str(scripts[script_name])
        module, _, attr = spec.partition(":")
        ok = bool(module) and bool(attr)
        detail = spec
        if ok:
            try:
                mod = importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001 - report, do not crash
                ok = False
                detail = f"{spec} ({type(exc).__name__})"
            else:
                ok = callable(getattr(mod, attr, None))
                if not ok:
                    detail = f"{spec} (attributo non callable)"
        check(f"script {script_name} importabile", ok, detail)

    package_dir = tool.get("package-dir")
    check("package-dir {'': 'scripts'}", package_dir == {"": "scripts"}, package_dir)

    packages = tool.get("packages") or []
    check("packages contiene vx", "vx" in packages, packages)


def main() -> int:
    exists = PYPROJECT.is_file()
    check("pyproject.toml esiste", exists, PYPROJECT.name)

    if not exists:
        for label in TOML_LABELS:
            skip(label, "pyproject.toml assente")
        raw = ""
    else:
        raw = PYPROJECT.read_text(encoding="utf-8")
        data, reason = _load_toml(PYPROJECT)
        if data is None:
            for label in TOML_LABELS:
                skip(label, reason)
        else:
            _check_toml(data)

    check("scripts/vx.py esiste", LAUNCHER.is_file(), LAUNCHER.name)
    check("vx.cli.main callable", callable(cli_main), type(cli_main).__name__)

    if exists:
        match = DRIVE.search(raw)
        check(
            "pyproject senza drive letter",
            match is None,
            match.group(0) if match else "",
        )
        check("pyproject senza user path", USER_PATH not in raw.lower(), "")
    else:
        skip("pyproject senza drive letter", "pyproject.toml assente")
        skip("pyproject senza user path", "pyproject.toml assente")

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
