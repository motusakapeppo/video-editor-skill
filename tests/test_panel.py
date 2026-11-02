"""Il pannello che si aggiorna da solo: una pagina, un file json, nessun server.

S1 - la pagina elenca tutte le fasi con il loro stato: fatta, in corso, da fare.
S2 - si ricarica da sola: il `<meta refresh>` c'e' e non dipende da javascript.
S3 - `progress.json` dice gli stessi numeri, per chi legge invece di guardare.
S4 - il testo dell'utente e' sfuggito: un prompt con `<script>` non diventa markup.
S5 - alla fine la barra e' al 100% e la nota dice l'esito.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import panel as panel_mod  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def main() -> int:
    phases = [("triage", "Guardo le clip"), ("render", "Renderizzo"), ("qc", "Controllo")]
    with tempfile.TemporaryDirectory(prefix="vx-panel-") as raw:
        target = Path(raw) / "progress.html"
        board = panel_mod.Panel(target, prompt="reel 30 secondi", clips=3, phases=phases)
        board.write()
        board.record(5.0, "triage", elapsed=1.0)
        board.record(25.0, "render", elapsed=8.0)

        page = target.read_text(encoding="utf-8")
        check(
            "S1 every phase is listed, with its state",
            page.count("<li") == 3
            and 'class="done"' in page
            and 'class="current"' in page,
            page.count("<li"),
        )
        check(
            "S2 the page reloads itself, without javascript",
            f'content="{panel_mod.REFRESH_S}"' in page and "<script" not in page,
            "meta refresh",
        )
        data = json.loads((Path(raw) / "progress.json").read_text(encoding="utf-8"))
        check(
            "S3 the json carries the same numbers",
            data["percent"] == 25.0
            and data["done"] == ["triage", "render"]
            and data["current"] == "render",
            {k: data[k] for k in ("percent", "done", "current")},
        )

        board.prompt = 'reel <script>alert(1)</script>'
        board.write()
        page = target.read_text(encoding="utf-8")
        check(
            "S4 the prompt is escaped, never markup",
            "<script>alert" not in page and "&lt;script&gt;" in page,
            "escaped",
        )

        board.finish("consegnato", elapsed=42.0)
        page = target.read_text(encoding="utf-8")
        data = json.loads((Path(raw) / "progress.json").read_text(encoding="utf-8"))
        check(
            "S5 at the end the bar is full and the note says how it went",
            data["percent"] == 100.0 and "consegnato" in page and data["current"] is None,
            data["note"],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
