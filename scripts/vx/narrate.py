"""vx.narrate — raccontare quello che la pipeline sta facendo, in italiano semplice.

Il codice emette gia' le fasi (`triage`, `ingest`, `bridge`, `render`, `qc`,
`deliverables`) con una percentuale. Questo modulo le traduce in una riga di
italiano, una per fase, e non ripete la stessa due volte.

Regole:

* si racconta **cosa succede**, non come e' implementato: "guardo le clip", non
  "asset_triage con soglie di upscale";
* una riga per fase, con la percentuale davanti, cosi' si sa a che punto e';
* se la fase cambia ma la percentuale no, la riga si stampa lo stesso: e' un
  passaggio, non un avanzamento;
* con `--json` non si stampa niente: il narratore tace, il documento parla.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, Optional, TextIO

#: stage -> (titolo, cosa sta succedendo). Le chiavi sono quelle emesse da
#: ``auto._notify``; una chiave sconosciuta si stampa con il suo nome, senza
#: inventare una descrizione.
PHASES: Dict[str, str] = {
    "triage": "Guardo le clip: durata, formato, audio, volto. Scarto quelle che non reggono.",
    "ingest": "Misuro quello che serve per decidere: parlato, silenzi, musica, movimento.",
    "bridge": "Scelgo i tagli: tolgo i tempi morti e tengo i pezzi che dicono qualcosa.",
    "render": "Costruisco la timeline e renderizzo. E' la parte lunga.",
    "render_done": "Il master e' scritto.",
    "qc": "Controllo il risultato: durata, loudness, picco, copertura dell'audio.",
    "deliverables": "Preparo le consegne.",
    "done": "Fatto.",
}

TOTAL_PHASES = len([key for key in PHASES if key != "done"])


class Narrator:
    """A progress observer that speaks Italian and never repeats a phase."""

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        panel: Optional[Any] = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock
        self.started = clock()
        self.seen: Dict[str, float] = {}
        self.panel = panel

    def __call__(self, percent: float, stage: str) -> None:
        key = str(stage)
        if key in self.seen:
            return
        self.seen[key] = float(percent)
        self._write(f"{float(percent):5.1f}%  {PHASES.get(key, key)}")
        if self.panel is not None:
            try:
                self.panel.record(float(percent), key, elapsed=self.clock() - self.started)
            except Exception:  # noqa: BLE001 - the panel is a viewer, not a gate
                pass

    def header(self, prompt: str, assets: int) -> None:
        self._write(f"Clip: {assets} nella cartella. Richiesta: \"{prompt}\"")

    def summary(self, report: dict) -> None:
        seconds = float(report.get("timeline_sec") or 0.0)
        master = report.get("master") or ""
        elapsed = self.clock() - self.started
        self._write(
            f"Fatto in {elapsed:.0f} s. Master di {seconds:.1f} s: {master}"
        )
        warnings = list(report.get("failures") or []) + list(report.get("warnings") or [])
        if warnings:
            self._write(f"Da sapere: {', '.join(str(item) for item in warnings)}")
        if self.panel is not None:
            try:
                self.panel.finish(
                    ", ".join(str(item) for item in warnings) or "consegnato",
                    elapsed=elapsed,
                )
            except Exception:  # noqa: BLE001
                pass

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text + "\n")
            self.stream.flush()
        except Exception:  # noqa: BLE001 - a closed stream must not break a render
            pass


__all__ = ["Narrator", "PHASES", "TOTAL_PHASES"]
