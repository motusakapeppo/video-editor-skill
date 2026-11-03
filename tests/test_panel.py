"""Il pannello che si aggiorna da solo: una pagina, un file json, nessun server.

S1 - la pagina elenca tutte le fasi con il loro stato: fatta, in corso, da fare.
S2 - si ricarica da sola: il `<meta refresh>` c'e' e non dipende da javascript.
S3 - `progress.json` dice gli stessi numeri, per chi legge invece di guardare.
S4 - il testo dell'utente e' sfuggito: un prompt con `<script>` non diventa markup.
S5 - alla fine la barra e' al 100% e la nota dice l'esito.
S6 - `say()` accoda una riga di racconto nel json: `t`, `phase`, `line`, `at`.
S7 - due righe identiche di fila non si duplicano e il tetto tiene le ultime.
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

        check_journal(Path(raw) / "racconto")
        check_master_guard(Path(raw))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


def check_master_guard(where: Path) -> None:
    """Un master annunciato e assente non si registra: la nota dice perche'."""
    folder = where / "guard"
    board = panel_mod.Panel(folder / "progress.html", phases=[("render", "Renderizzo")])
    board.record(85.0, "render", elapsed=3.0)
    missing = where / "manca" / "master.mp4"
    board.finish("consegnato", elapsed=9.0, master=str(missing))

    data = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
    check(
        "S8 finish() with a master that is not on disk does not register it",
        data["master"] == "" and data["percent"] == 85.0 and data["current"] is None,
        {k: data[k] for k in ("master", "percent", "current")},
    )
    check(
        "S8 and the note says why, in Italian, without claiming it was done",
        "fermato prima della fine" in data["note"]
        and "il master non esiste" in data["note"]
        and str(missing) in data["note"],
        data["note"],
    )


def check_journal(where: Path) -> None:
    """`say()` scrive il racconto nel json: righe, tempo, fase, niente eco."""
    now = [0.0]
    target = where / "progress.html"
    board = panel_mod.Panel(
        target,
        phases=[("triage", "Guardo le clip")],
        clock=lambda: now[0],
    )
    board.record(5.0, "triage", elapsed=0.0)
    board.say("Misuro 12 clip: 4 scartate, 8 buone", phase="triage")
    now[0] = 7.5
    board.say("Misuro 12 clip: 4 scartate, 8 buone", phase="triage")
    board.say("Taglio 3 secondi di silenzio in 5 punti", phase="bridge")

    data = json.loads((where / "progress.json").read_text(encoding="utf-8"))
    rows = data["journal"]
    check(
        "S6 say() appends the Italian line to the json, not only to the page",
        len(rows) == 2
        and rows[0]["line"] == "Misuro 12 clip: 4 scartate, 8 buone"
        and rows[1]["line"] == "Taglio 3 secondi di silenzio in 5 punti",
        [row["line"] for row in rows],
    )
    check(
        "S6 every row carries t, phase, line and the ISO instant",
        all(
            set(row) == {"t", "phase", "line", "at"}
            and isinstance(row["t"], float)
            and row["at"].endswith("+00:00")
            for row in rows
        ),
        rows[1],
    )
    check(
        "S6 t is the elapsed seconds since the panel started",
        rows[0]["t"] == 0.0 and rows[1]["t"] == 7.5 and rows[1]["phase"] == "bridge",
        [row["t"] for row in rows],
    )
    check(
        "S7 the same line twice in a row is not echoed",
        [row["line"] for row in rows].count("Misuro 12 clip: 4 scartate, 8 buone") == 1,
        len(rows),
    )

    for index in range(panel_mod.MAX_JOURNAL + 5):
        board.say(f"riga {index}")
    rows = json.loads((where / "progress.json").read_text(encoding="utf-8"))["journal"]
    check(
        "S7 past the ceiling the newest lines stay, the oldest go",
        len(rows) == panel_mod.MAX_JOURNAL
        and rows[-1]["line"] == f"riga {panel_mod.MAX_JOURNAL + 4}"
        and rows[0]["line"] == "riga 5",
        [len(rows), rows[0]["line"], rows[-1]["line"]],
    )


if __name__ == "__main__":
    raise SystemExit(main())
