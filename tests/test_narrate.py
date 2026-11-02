"""La skill racconta cosa sta facendo, in italiano, una fase alla volta.

Scenario:

S1 - ogni fase emessa dal pipeline diventa una riga di italiano, con la
     percentuale davanti; le fasi note hanno un testo che dice cosa succede,
     non come e' fatto il codice.
S2 - la stessa fase non si ripete: il narratore parla una volta per fase.
S3 - una fase sconosciuta si stampa con il suo nome, senza inventare una
     descrizione.
S4 - l'intestazione dice quante clip e quale richiesta; il riepilogo dice tempo
     impiegato, durata del master e gli avvisi, se ci sono.
S5 - uno stream chiuso non fa esplodere il render: il narratore tace.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import narrate  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def main() -> int:
    stream = io.StringIO()
    clock_values = iter([0.0, 12.0, 42.5])
    narrator = narrate.Narrator(stream, clock=lambda: next(clock_values, 42.5))

    narrator.header("reel verticale 30 secondi", 5)
    for percent, stage in (
        (5.0, "triage"),
        (20.0, "ingest"),
        (22.0, "bridge"),
        (25.0, "render"),
        (92.0, "render_done"),
        (96.0, "qc"),
        (100.0, "deliverables"),
    ):
        narrator(percent, stage)
    narrator(96.0, "qc")  # ripetuta di proposito
    narrator(100.0, "strano")
    narrator.summary({"timeline_sec": 30.0, "master": "out/master.mp4", "failures": ["master_true_peak"]})

    text = stream.getvalue()
    lines = [line for line in text.splitlines() if line.strip()]

    check(
        "S1 the header says how many clips and the request",
        "5 nella cartella" in lines[0] and "reel verticale 30 secondi" in lines[0],
        lines[0][:70],
    )
    ordered = [line for line in lines if line.strip().startswith(tuple("0123456789 "))]
    check(
        "S1 each phase is one Italian line with its percentage",
        len(ordered) >= 7 and ordered[0].strip().startswith("5.0%") and "Guardo le clip" in ordered[0],
        ordered[0][:60] if ordered else "",
    )
    leaked = [
        line
        for line in ordered
        if any(token in line for token in ("asset_triage", "_notify", "render_done", "bridge:"))
    ]
    check(
        "S1 the descriptions do not leak code names",
        not leaked,
        leaked[:2],
    )
    check(
        "S2 a repeated phase is not repeated",
        text.count("Controllo il risultato") == 1,
        text.count("Controllo il risultato"),
    )
    check(
        "S3 an unknown phase prints its own name",
        "strano" in text,
        "strano",
    )
    check(
        "S4 the summary carries time, duration and warnings",
        "Fatto in" in text and "30.0 s" in text and "master_true_peak" in text,
        lines[-1][:70],
    )

    class Broken(io.StringIO):
        def write(self, *args, **kwargs):  # noqa: D102
            raise OSError("chiuso")

    try:
        broken = narrate.Narrator(Broken())
        broken(1.0, "triage")
        broken.summary({})
        closed_ok = True
    except Exception:  # noqa: BLE001
        closed_ok = False
    check("S5 a closed stream never breaks a render", closed_ok, "nessuna eccezione")

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
