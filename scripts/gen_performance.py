"""vx performance report — every number with the command that produced it.

The report is generated, never typed: it runs the measurements it can run here and
lists, for each one, the exact command a person can repeat. Numbers that belong to
a test are cited with the test's name and a pointer to run it, never copied from
memory (the project's rule: a number exists only if it was measured).

    python scripts/gen_performance.py            # measure and write PLAN/PERFORMANCE.md
    python scripts/gen_performance.py --check    # exit 1 if the file is missing rows
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render  # noqa: E402

TARGET = ROOT / "PLAN" / "PERFORMANCE.md"
TWO_HOURS = 7200.0
CLIP_SECONDS = 4.0


def measure_compile() -> Dict[str, Any]:
    """Compile a two-hour timeline of 1800 clips; the number is validate + compile."""
    source = ROOT / "tests" / "stock_corpus" / "bright_45.mp4"
    if not source.is_file():
        raise SystemExit(f"missing fixture: {source}")
    clips = []
    count = int(TWO_HOURS / CLIP_SECONDS)
    for index in range(count):
        clips.append(
            edl3.Clip3(
                id=f"c{index}",
                source=str(source),
                src_in=0.0,
                src_out=CLIP_SECONDS,
                dst_in=index * CLIP_SECONDS,
                dst_out=(index + 1) * CLIP_SECONDS,
            )
        )
    timeline = edl3.Timeline3(fps=24, size=(1080, 1920), clips=clips)
    started = time.perf_counter()
    problems = timeline.validate()
    plan = edl3_render.compile_timeline(timeline, audio="drop")
    elapsed = time.perf_counter() - started
    return {
        "clips": count,
        "seconds": round(elapsed, 3),
        "problem_codes": sorted({p["code"] for p in problems}),
        "filters": len(plan["filter_script"].splitlines()),
    }


def measure_document() -> str:
    compile_row = measure_compile()
    lines: List[str] = []
    lines.append("# Referto di prestazioni")
    lines.append("")
    lines.append(
        "Generato da `python scripts/gen_performance.py`. Ogni riga porta la misura e"
    )
    lines.append(
        "il comando che l'ha prodotta: nessun numero e' copiato da un'altra sessione."
    )
    lines.append("")
    lines.append("| misura | valore | comando |")
    lines.append("|---|---|---|")
    lines.append(
        f"| compilazione di una timeline da 2 ore ({compile_row['clips']} clip) "
        f"| **{compile_row['seconds']} s**, {compile_row['filters']} righe di grafo "
        f"| `python scripts/gen_performance.py` (misura interna) |"
    )
    lines.append(
        "| frame identici in serie e in parallelo, con il tempo di entrambe "
        "| vedi `[INFO] tempo misurato` nel test "
        "| `python tests/test_parallel_chunks.py` |"
    )
    lines.append(
        "| chunk riusati quando nulla cambia, e solo quelli toccati quando cambia "
        "| vedi `rendered`/`reused` nel test "
        "| `python tests/test_smart_render.py` |"
    )
    lines.append(
        "| fedelta' di un encoder candidato (MAD sul frame decodificato) "
        "| soglia dichiarata `2.0` "
        "| `python tests/test_encoder_fidelity.py` |"
    )
    lines.append(
        "| peso dell'intermedio lossless rispetto alla consegna "
        "| vedi `[INFO] peso dichiarato` "
        "| `python tests/test_lossless.py` |"
    )
    lines.append(
        "| suite completa (runner, assert, durata) "
        "| vedi la riga finale di `run_all` "
        "| `python tests/run_all.py` |"
    )
    lines.append("")
    lines.append("## Codici di validazione incontrati")
    lines.append("")
    codes = compile_row["problem_codes"] or ["nessuno"]
    lines.append(", ".join(f"`{code}`" for code in codes))
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        if not TARGET.is_file():
            print(f"{TARGET.name}: missing, run the generator")
            return 1
        text = TARGET.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
        commands = [row for row in rows if "python " in row]
        if len(commands) < 5:
            print(f"{TARGET.name}: {len(commands)} row(s) with a command, expected 5+")
            return 1
        print(f"{TARGET.name}: {len(commands)} measured row(s), each with its command")
        return 0

    TARGET.write_text(measure_document(), encoding="utf-8")
    print(f"{TARGET.name}: written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
