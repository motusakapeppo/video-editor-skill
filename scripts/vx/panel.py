"""vx.panel — un pannello HTML che si aggiorna da solo mentre la skill lavora.

Il file e' autocontenuto e si ricarica con un `<meta refresh>`: si apre una volta
nel pannello di anteprima e da li' in poi cambia da solo, senza server, senza
websocket, senza dipendenze. Ogni fase ha tre stati — fatta, in corso, da fare —
e c'e' la barra del tempo.

Serve a due cose: chi guarda vede dove siamo senza leggere un log, e `vx serve`
(livello 3) legge lo **stesso** `progress.json` invece di inventare un secondo
formato. Il JSON si scrive sempre accanto a questo HTML; per `auto` la cartella
del pannello e' la **workdir risolta** (quella di `plan.json` e `report.json`),
non la cartella del master, cosi' `vx serve <workdir>` trova il progresso.
Un'eventuale copia del solo HTML accanto al master e' una comodita': la fonte
di verita' resta una, il `progress.json` nella workdir.
"""

from __future__ import annotations

import html
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import degrade

REFRESH_S = 1

#: Quante righe di racconto il json porta con se'. Il tetto tiene le **ultime**:
#: un lavoro lungo racconta la coda, non l'inizio, e il file non cresce senza fine.
MAX_JOURNAL = 200

STYLE = """
:root { color-scheme: dark; }
body { margin: 0; padding: 24px; background: #111214; color: #e8e6e3;
       font: 15px/1.5 ui-monospace, "JetBrains Mono", Consolas, monospace; }
h1 { font-size: 18px; margin: 0 0 4px; font-weight: 600; letter-spacing: .02em; }
.muted { color: #8b8f96; }
.bar { height: 6px; background: #23262b; border-radius: 3px; margin: 16px 0 24px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: #4ea1ff; transition: width .4s ease; }
ol { list-style: none; margin: 0; padding: 0; }
li { padding: 7px 0 7px 26px; position: relative; color: #8b8f96; }
li.done { color: #9fe3a6; }
li.current { color: #ffffff; }
li.current::after { content: "…"; position: absolute; right: 0; }
li .mark { position: absolute; left: 0; width: 18px; text-align: center; }
li.done .mark { color: #9fe3a6; }
li.current .mark { color: #4ea1ff; }
footer { margin-top: 22px; color: #8b8f96; font-size: 13px; }
footer b { color: #e8e6e3; font-weight: 600; }
"""


def _atomic(path: Path, text: str) -> None:
    """Scrive il file accanto e lo sostituisce: chi legge vede prima o dopo.

    Se la scrittura o la sostituzione falliscono, il temporaneo si toglie e
    l'errore risale al chiamante: il file precedente resta intero. Chi non vuole
    fermarsi lo gestisce in :meth:`Panel.write`, che e' l'unica porta.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _real_file(path: Path) -> bool:
    """Un file c'e' solo se ha byte dentro: un percorso nudo non e' un master."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


@dataclass
class Panel:
    """One HTML file, rewritten at every phase, that reloads itself."""

    path: Path
    prompt: str = ""
    clips: int = 0
    phases: Sequence[tuple] = ()  # (key, label) in display order
    done: List[str] = field(default_factory=list)
    current: Optional[str] = None
    percent: float = 0.0
    elapsed: float = 0.0
    note: str = ""
    master: str = ""
    #: Le righe di racconto, in ordine: cosa sta succedendo, non solo dove siamo.
    journal: List[Dict[str, Any]] = field(default_factory=list)
    clock: Callable[[], float] = time.monotonic
    started: Optional[float] = None
    #: Se c'e' un server acceso su questo lavoro, il pannello ci porta invece di
    #: restare un mondo a parte: il pannello semplice e' un ripiego, non la vista.
    watch_url: str = ""
    #: Copia del **solo HTML** in un secondo posto (comodita' accanto al master).
    #: Il json non si duplica: `progress.json` vive nella cartella del pannello.
    copy: Optional[Path] = None
    #: I cedimenti di questo pannello (nome -> motivo). La pagina e' un modo di
    #: guardare, non un cancello: se non si scrive, il lavoro continua e questo
    #: elenco lo dice (e finisce in ``report["degraded"]`` via :mod:`vx.degrade`).
    degraded: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.started is None:
            self.started = self.clock()

    def say(self, line: str, *, phase: Optional[str] = None) -> Dict[str, Any]:
        """Accoda una riga di italiano e la pubblica nel json.

        Il journal e' **dati**, non console: chi stampa e' chi chiama. La riga
        porta il tempo trascorso dall'inizio del lavoro, la fase e il momento in
        cui e' stata scritta; due righe identiche di fila non si duplicano, e
        oltre :data:`MAX_JOURNAL` restano le ultime.
        """
        text = str(line).strip()
        if not text:
            return {}
        now = self.clock()
        base = self.started if self.started is not None else now
        row: Dict[str, Any] = {
            "t": round(max(0.0, now - base), 1),
            "phase": str(phase or self.current or ""),
            "line": text,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if not (self.journal and str(self.journal[-1].get("line", "")) == text):
            self.journal.append(row)
            if len(self.journal) > MAX_JOURNAL:
                del self.journal[: len(self.journal) - MAX_JOURNAL]
        self.write()
        return row

    def record(self, percent: float, stage: str, *, elapsed: float = 0.0) -> None:
        key = str(stage)
        if key not in [item[0] for item in self.phases]:
            self.phases = list(self.phases) + [(key, key)]
        if key not in self.done:
            self.done.append(key)
        self.current = key
        self.percent = float(percent)
        self.elapsed = float(elapsed)
        self.write()

    def finish(
        self, note: str = "", *, elapsed: float = 0.0, master: str = ""
    ) -> None:
        """Chiude il lavoro, ma solo quando il lavoro e' davvero finito.

        La barra arriva al 100% **solo** con un master che esiste su disco: un
        percorso annunciato e assente non si registra, la percentuale resta
        dov'era e la nota dice il motivo. Un lavoro che si e' fermato a meta'
        non puo' quindi piu' dichiararsi concluso.
        """
        self.current = None
        self.elapsed = float(elapsed)
        text = str(note or "")
        claimed = str(master or "")
        if claimed and not _real_file(Path(claimed)):
            self.master = ""
            reason = f"fermato prima della fine: il master non esiste ({claimed})"
            self.note = f"{text} - {reason}" if text else reason
            self.write()
            return
        self.master = claimed
        self.percent = 100.0
        self.note = text
        self.write()

    def stop(self, note: str = "", *, elapsed: Optional[float] = None) -> None:
        """Il lavoro si e' fermato: la percentuale resta, il master non si inventa."""
        self.current = None
        if elapsed is not None:
            self.elapsed = float(elapsed)
        self.note = str(note or "")
        self.write()

    def write(self) -> Path:
        """Scrive pagina e json **atomicamente**, senza mai fermare il lavoro.

        Con piu' lavori in parallelo un lettore puo' aprire il file mentre viene
        riscritto: senza `os.replace` vedrebbe un JSON troncato e la pagina
        lampeggerebbe su "in attesa". Il file temporaneo si scrive accanto e si
        sostituisce in un colpo solo.

        Un pannello e' un modo di guardare, non un cancello: permessi negati,
        disco pieno o una cartella che non si crea diventano una voce in
        :attr:`degraded` (e in :data:`vx.degrade`) e la funzione ritorna il
        percorso. Il file gia' scritto resta intatto: la sostituzione atomica o
        riesce o non tocca niente.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _atomic(self.path, self.html())
            _atomic(
                self.path.parent / "progress.json",
                json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n",
            )
            if self.copy is not None and Path(self.copy) != Path(self.path):
                _atomic(self.copy, self.html())
        except Exception as exc:  # noqa: BLE001 - la vista non e' il lavoro
            self.degraded.setdefault("panel", f"{type(exc).__name__}: {exc}")
            degrade.note("panel", exc)
        return self.path

    def as_dict(self) -> Dict[str, object]:
        return {
            "prompt": self.prompt,
            "clips": self.clips,
            "percent": round(self.percent, 1),
            "elapsed_sec": round(self.elapsed, 1),
            "done": list(self.done),
            "current": self.current,
            "note": self.note,
            "master": self.master,
            "phases": [{"key": key, "label": label} for key, label in self.phases],
            "journal": [dict(row) for row in self.journal],
        }

    def html(self) -> str:
        rows = []
        for key, label in self.phases:
            if self.current == key:
                cls, mark = "current", "▸"
            elif key in self.done:
                cls, mark = "done", "✓"
            else:
                cls, mark = "", " "
            rows.append(
                f'<li class="{cls}"><span class="mark">{mark}</span>{html.escape(label)}</li>'
            )
        head = html.escape(self.prompt) if self.prompt else ""
        meta = (
            f'<div class="muted">Clip: {self.clips} · {self.elapsed:.0f} secondi'
            + (f' · {html.escape(self.note)}' if self.note else "")
            + "</div>"
            if head
            else ""
        )
        target = html.escape(self.watch_url)
        if target:
            refresh = f'<meta http-equiv="refresh" content="1; url={target}">'
            banner = (
                '<p class="muted">Ti apro la vista completa: '
                f'<a href="{target}">{target}</a></p>'
            )
        else:
            refresh = f'<meta http-equiv="refresh" content="{REFRESH_S}">'
            banner = ""
        return (
            "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
            + refresh
            + "<title>vx - montaggio in corso</title><style>" + STYLE + "</style></head><body>"
            f"<h1>{head or 'Montaggio in corso'}</h1>{meta}{banner}"
            f'<div class="bar"><i style="width:{max(0.0, min(100.0, self.percent)):.1f}%"></i></div>'
            "<ol>" + "".join(rows) + "</ol>"
            f'<footer><b>{self.percent:.0f}%</b> · {self.elapsed:.0f} secondi'
            + (f" · {html.escape(self.note)}" if self.note else "")
            + "</footer></body></html>\n"
        )


__all__ = ["Panel", "REFRESH_S", "MAX_JOURNAL"]
