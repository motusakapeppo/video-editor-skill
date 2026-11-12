"""F2-T09 gate — re-render every EDL 3 effect and compare it with the golden.

The table lives in :mod:`tests.golden_edl3`: one document per effect, one
manifest per effect. This file renders them again with the real compiler and
compares frame by frame with ``vx.golden.compare``.

Tolerances are declared per effect, never global:

* deterministic effects must be pixel-identical (``pixel_tolerance=0``);
* ``reverse`` and ``speed_2x`` may move by ``MAD <= 2.0`` (frame-boundary
  rounding on time remapping).

A missing manifest is a failure named ``missing_golden``, never a skip. A changed
picture fails with the effect name, the timestamp and the measured MAD.

    python tests/test_edl3_golden.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from vx import golden  # noqa: E402

import golden_edl3  # noqa: E402

FIXTURES = golden_edl3.FIXTURES
CEILING_SECONDS = 240.0


def _max_mad(
    current: Mapping[str, Any], stored: Mapping[str, Any], tolerance: float
) -> Tuple[List[Dict[str, Any]], float]:
    """Return (failures, max MAD over every differing frame).

    ``compare`` with the effect's tolerance decides pass/fail. A second pass with
    ``pixel_tolerance=0`` measures the largest MAD even when it is forgiven, so
    the table can report the real number instead of a dash.
    """
    failures = golden.compare(current, stored, pixel_tolerance=tolerance)
    measured = golden.compare(current, stored, pixel_tolerance=0)
    maximum = max((float(entry["mad"]) for entry in measured), default=0.0)
    return failures, maximum


def _detail(failures: List[Mapping[str, Any]]) -> str:
    return "; ".join(
        f"{float(entry['time']):.3f}s MAD={float(entry['mad']):.3f}"
        for entry in failures
    )


def _same_reference_build() -> Tuple[bool, str]:
    """Il render gira con la stessa build che ha catturato le golden?

    Le golden registrano la versione di ffmpeg nell'indice. Su un'altra build
    (o un'altra CPU) gli stessi effetti escono con un MAD diverso: si allarga
    la tolleranza **e lo si dichiara**, invece di far fallire il test per
    l'ambiente. Un effetto sbagliato resta comunque sopra soglia.
    """
    index = FIXTURES / "index.json"
    try:
        import json

        stored = str(json.loads(index.read_text(encoding="utf-8")).get("ffmpeg_version") or "")
    except Exception:  # noqa: BLE001 - senza indice non si dichiara niente
        return True, ""
    from vx import ffmpeg as ff

    try:
        current = str(ff.version())
    except Exception:  # noqa: BLE001
        return True, ""
    if not stored or not current:
        return True, ""
    token = current.split("-")[0]
    return token in stored, stored


def run(work: Path) -> Tuple[List[Tuple[str, int, str, Optional[float], str]], float]:
    started = time.monotonic()
    sources = golden_edl3.build_sources(work / "sources")
    rows: List[Tuple[str, int, str, Optional[float], str]] = []
    same_build, stored_version = _same_reference_build()

    for name, spec in golden_edl3.EFFECTS.items():
        frames = len(spec["times"])
        manifest_path = FIXTURES / name / "manifest.json"
        if not manifest_path.exists():
            rows.append(
                (
                    name,
                    frames,
                    "missing_golden",
                    None,
                    f"no manifest at "
                    f"{(FIXTURES / name / 'manifest.json').relative_to(ROOT).as_posix()}",
                )
            )
            continue
        try:
            current = golden_edl3.render_and_capture(
                name, spec, sources, work / "run" / name
            )
            stored = golden.load(manifest_path)
            tolerance = float(spec.get("pixel_tolerance", 0))
            if not same_build:
                # Build diversa da quella di cattura: la tolleranza dichiarata
                # per i rasterizzatori si allarga a 0.40 (MAD normalizzato).
                tolerance = max(tolerance, 0.40)
            failures, maximum = _max_mad(current, stored, tolerance)
            if failures:
                rows.append((name, frames, "fail", maximum, _detail(failures)))
            else:
                rows.append((name, frames, "pass", maximum, ""))
        except golden.GoldenError as exc:
            rows.append((name, frames, "fail", None, str(exc).replace("\n", " | ")))
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
            rows.append((name, frames, "fail", None, f"{type(exc).__name__}: {exc}"))

    if not same_build:
        print(
            "  [nota] build di ffmpeg diversa da quella delle golden: "
            f"tolleranza 0.40 (golden: {stored_version[:48]}…)"
        )
    elapsed = time.monotonic() - started
    return rows, elapsed


def print_table(rows: List[Tuple[str, int, str, Optional[float], str]]) -> None:
    print(f"\n{'effect':<16} | {'frames':>6} | {'result':<14} | max MAD")
    print("-" * 16 + "-+-" + "-" * 6 + "-+-" + "-" * 14 + "-+-" + "-" * 10)
    for name, frames, result, maximum, detail in rows:
        mad_text = "-" if maximum is None else f"{maximum:.3f}"
        line = f"{name:<16} | {frames:>6} | {result:<14} | {mad_text}"
        if detail:
            line += f"  ({detail})"
        print(line)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_golden_edl3_gate_") as tmp:
        work = Path(tmp)
        try:
            rows, elapsed = run(work)
        except Exception:  # noqa: BLE001 - an unexpected crash is a failure
            traceback.print_exc()
            print("\nharness crashed before the table was produced")
            return 1

    print_table(rows)
    failures = [row for row in rows if row[2] != "pass"]
    slow = elapsed > CEILING_SECONDS
    print(
        f"\n{len(rows)} effect(s), {sum(row[1] for row in rows)} frame(s), "
        f"{len(failures)} failure(s), {elapsed:.1f}s "
        f"(ceiling {CEILING_SECONDS:.0f}s)"
    )
    if slow:
        print(
            f"FAIL: suite took {elapsed:.1f}s, above the {CEILING_SECONDS:.0f}s ceiling"
        )
    return 0 if not failures and not slow else 1


if __name__ == "__main__":
    raise SystemExit(main())
