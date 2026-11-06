"""``vx capabilities``: le 25 capability di ffmpeg, in tabella e in JSON.

Le capability esistevano solo dentro ``doctor --json``: chi voleva sapere "cosa
perdo senza scdet" doveva leggere un blob o indovinare. Questo verbo le mostra
con il verdetto dell'ambiente, la versione dei due binari e, per ogni assenza,
la conseguenza concreta sulla pipeline.

S9a  exit 0 e output con ffmpeg, ffprobe e il conteggio delle capability;
S9b  ``--json`` valido, con lo schema pinnato (25 chiavi, tipi, exit_code);
S9c  una capability assente (patch di ``ff.capabilities``) degrada il verdetto,
     dice cosa si perde, e con libx264 mancante esce 4 come ``doctor`` broken.

Nessuna rete: solo i binari locali e le loro risposte.
python tests/test_capabilities.py
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, doctor  # noqa: E402
from vx import ffmpeg as ff  # noqa: E402

PASS = 0
FAIL = 0

#: Lo schema del referto macchina: se cambia, cambia qui, non di nascosto.
SCHEMA_KEYS = {
    "schema",
    "verdict",
    "ok",
    "exit_code",
    "ffmpeg",
    "ffprobe",
    "capabilities",
    "present",
    "total",
    "missing",
    "missing_required",
    "deps",
    "deps_missing",
    "lost",
    "warnings",
}


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def cli_run(argv: list) -> tuple:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


@contextlib.contextmanager
def patched_capabilities(**flags: bool):
    """Sostituisce ``ff.capabilities`` per un giro: nessun binario si tocca."""
    real = ff.capabilities()
    fake = dict(real)
    fake.update(flags)
    original = ff.capabilities
    ff.capabilities = lambda: dict(fake)  # type: ignore[assignment]
    try:
        yield fake
    finally:
        ff.capabilities = original  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# S9a - tabella umana
# --------------------------------------------------------------------------- #


def test_table() -> None:
    code, out, _err = cli_run(["capabilities"])
    check("S9a: exit 0", code == 0, code)
    check("S9a: l'output nomina ffmpeg", "ffmpeg" in out)
    check("S9a: l'output nomina ffprobe", "ffprobe" in out)
    match = re.search(r"capacita'\s+(\d+)/(\d+)", out)
    check(
        "S9a: il conteggio delle capability presenti c'e'",
        match is not None and match.group(2) == str(len(doctor.REQUIRED_CAPS)),
        match.group(0) if match else out.splitlines()[:5],
    )
    check(
        "S9a: tutte le 25 capability sono in tabella",
        all(name in out for name in doctor.REQUIRED_CAPS),
        [name for name in doctor.REQUIRED_CAPS if name not in out],
    )
    check(
        "S9a: il verdetto e' dichiarato in italiano",
        any(word in out.lower() for word in ("pronto", "degradato", "rotto")),
        out.splitlines()[:2],
    )


# --------------------------------------------------------------------------- #
# S9b - referto macchina
# --------------------------------------------------------------------------- #


def test_json() -> None:
    code, out, _err = cli_run(["capabilities", "--json"])
    try:
        report = json.loads(out)
    except ValueError as exc:
        check("S9b: --json e' JSON valido", False, f"{exc}: {out[:120]}")
        return
    check("S9b: --json e' JSON valido", isinstance(report, dict))
    check("S9b: lo schema e' pinnato", SCHEMA_KEYS <= set(report), sorted(SCHEMA_KEYS - set(report)))
    check("S9b: schema dichiarato", report.get("schema") == "vx.capabilities/1", report.get("schema"))
    check("S9b: exit_code coerente col codice", int(report["exit_code"]) == code, (report["exit_code"], code))
    check(
        "S9b: capabilities ha esattamente le 25 chiavi vere",
        set(report["capabilities"]) == set(doctor.REQUIRED_CAPS)
        and len(report["capabilities"]) == 25,
        len(report["capabilities"]),
    )
    check(
        "S9b: present + missing fanno 25",
        int(report["present"]) + len(report["missing"]) == 25,
        (report["present"], len(report["missing"])),
    )
    check(
        "S9b: ffmpeg e ffprobe portano percorso e versione",
        isinstance(report["ffmpeg"], dict)
        and isinstance(report["ffprobe"], dict)
        and (report["ffmpeg"].get("version") or report["ffmpeg"].get("error"))
        and (report["ffprobe"].get("version") or report["ffprobe"].get("error")),
        (report["ffmpeg"].get("version"), report["ffprobe"].get("version")),
    )
    check(
        "S9b: verdict e' uno dei tre dichiarati",
        report.get("verdict") in ("ready", "degraded", "broken"),
        report.get("verdict"),
    )
    check(
        "S9b: lost e' una lista di {capability, what}",
        isinstance(report["lost"], list)
        and all(set(item) == {"capability", "what"} for item in report["lost"]),
        report["lost"][:2],
    )


# --------------------------------------------------------------------------- #
# S9c - una capability assente non si nasconde
# --------------------------------------------------------------------------- #


def test_missing_capability() -> None:
    with patched_capabilities(scdet=False):
        code, out, _err = cli_run(["capabilities"])
        json_code, json_out, _jerr = cli_run(["capabilities", "--json"])
    report = json.loads(json_out)
    check("S9c: senza scdet l'exit resta 0 (non e' obbligatoria)", code == 0, code)
    check(
        "S9c: la tabella dice cosa si perde",
        "scdet" in out and "scene cut" in out,
        [line for line in out.splitlines() if "scdet" in line],
    )
    check(
        "S9c: il verdetto degrada senza mentire",
        "DEGRADATO" in out and report["verdict"] == "degraded",
        (report["verdict"], report["missing"]),
    )
    check(
        "S9c: il JSON elenca scdet fra le mancanti",
        "scdet" in report["missing"] and "scdet" in report["capabilities"],
        report["missing"],
    )
    check(
        "S9c: il JSON dice la conseguenza",
        any(
            item["capability"] == "scdet" and "scene cut" in item["what"]
            for item in report["lost"]
        ),
        report["lost"],
    )
    check("S9c: il JSON resta coerente col codice", int(report["exit_code"]) == json_code, json_code)

    with patched_capabilities(libx264=False):
        code, out, _err = cli_run(["capabilities"])
        _, json_out, _jerr = cli_run(["capabilities", "--json"])
    report = json.loads(json_out)
    check(
        "S9c: senza libx264 si esce 4 come doctor broken",
        code == 4 and int(report["exit_code"]) == 4,
        (code, report["exit_code"]),
    )
    check("S9c: il verdetto e' rotto", "ROTTO" in out and report["verdict"] == "broken", report["verdict"])
    check(
        "S9c: dice che il render non parte",
        any(item["capability"] == "libx264" and "render" in item["what"] for item in report["lost"]),
        report["lost"],
    )
    check(
        "S9c: libx264 e' fra le dipendenze obbligatorie",
        "libx264" in report["missing_required"],
        report["missing_required"],
    )


def main() -> int:
    test_table()
    test_json()
    test_missing_capability()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
