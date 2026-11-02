"""vx.panel — un pannello HTML che si aggiorna da solo mentre la skill lavora.

Il file e' autocontenuto e si ricarica con un `<meta refresh>`: si apre una volta
nel pannello di anteprima e da li' in poi cambia da solo, senza server, senza
websocket, senza dipendenze. Ogni fase ha tre stati — fatta, in corso, da fare —
e c'e' la barra del tempo.

Serve a due cose: chi guarda vede dove siamo senza leggere un log, e `vx serve`
(livello 3) partira' dallo stesso `progress.json` invece di inventare un secondo
formato.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REFRESH_S = 1

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
    """Scrive il file accanto e lo sostituisce: chi legge vede prima o dopo."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


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

    def finish(self, note: str = "", *, elapsed: float = 0.0, master: str = "") -> None:
        self.current = None
        self.percent = 100.0
        self.elapsed = float(elapsed)
        self.note = note
        self.master = str(master or "")
        self.write()

    def write(self) -> Path:
        """Scrive pagina e json **atomicamente**.

        Con piu' lavori in parallelo un lettore puo' aprire il file mentre viene
        riscritto: senza `os.replace` vedrebbe un JSON troncato e la pagina
        lampeggerebbe su "in attesa". Il file temporaneo si scrive accanto e si
        sostituisce in un colpo solo.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic(self.path, self.html())
        _atomic(
            self.path.parent / "progress.json",
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n",
        )
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
        return (
            "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
            f'<meta http-equiv="refresh" content="{REFRESH_S}">'
            "<title>vx - montaggio in corso</title><style>" + STYLE + "</style></head><body>"
            f"<h1>{head or 'Montaggio in corso'}</h1>{meta}"
            f'<div class="bar"><i style="width:{max(0.0, min(100.0, self.percent)):.1f}%"></i></div>'
            "<ol>" + "".join(rows) + "</ol>"
            f'<footer><b>{self.percent:.0f}%</b> · {self.elapsed:.0f} secondi'
            + (f" · {html.escape(self.note)}" if self.note else "")
            + "</footer></body></html>\n"
        )


__all__ = ["Panel", "REFRESH_S"]
