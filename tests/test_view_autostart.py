"""La vista parte da sola: nessuno deve chiamare `vx up` per vederla.

    python tests/test_view_autostart.py

Il patto nuovo: quando parte un lavoro (`vx auto`, `vx reel`, `vx_auto` MCP) la
pagina e' gia' accesa e il suo indirizzo risponde. L'unico modo per non
accenderla e' dirlo: `--no-view` o `VX_NO_VIEW=1` (lo usa la suite).

Il browser **non** si apre in questa prova: `ensure_view(open_page=False)`
accende il daemon per la stessa strada di `autostart_view`, e la seconda
chiamata riusa il daemon vivo (quindi nemmeno `autostart_view` aprirebbe una
scheda). La scheda non e' il soggetto: il soggetto e' che l'indirizzo risponda.
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
import webbrowser
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Il daemon scrive un registro di sessione: qui, non in quello dell'utente.
_SESSION = tempfile.mkdtemp(prefix="vx-autostart-sessions-")
os.environ["VX_SESSION_DIR"] = _SESSION

from vx import auto, cli  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def down() -> None:
    """Ferma il daemon del test, senza toccare altri processi."""
    record = cli._read_up()
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid:
        cli._stop_pid(pid)
    try:
        cli._up_file().unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    down()
    try:
        print("\n-- spenta: due modi per dirlo, nessun daemon")
        os.environ["VX_NO_VIEW"] = "1"
        check(
            "V0 VX_NO_VIEW=1 non accende niente",
            auto.autostart_view() == "" and not cli._read_up(),
            cli._read_up(),
        )
        os.environ.pop("VX_NO_VIEW", None)
        check(
            "V0 --no-view non accende niente",
            auto.autostart_view(Namespace(no_view=True)) == "",
            "",
        )

        print("\n-- accesa: la stessa strada di `vx up`")
        url = cli.ensure_view(open_page=False)
        check("V1 l'indirizzo c'e'", bool(url) and url.startswith("http://127.0.0.1:"), url)
        page = get(url) if url else b""
        check(
            "V1 la pagina risponde davvero",
            b"Lavoro in corso" in page or b"<html" in page.lower(),
            len(page),
        )
        again = cli.ensure_view(open_page=False)
        check("V1 due chiamate, un daemon solo", again == url, (url, again))

        record = cli._read_up()
        check(
            "V2 il daemon e' vivo e registrato",
            bool(record.get("pid")) and bool(record.get("url")),
            {"pid": record.get("pid"), "url": record.get("url")},
        )

        print("\n-- automatico: la funzione che chiamano auto, reel e l'MCP")
        opened: list = []
        original = webbrowser.open

        def spy(url: str, *args: object, **kwargs: object) -> bool:
            opened.append(str(url))
            return True

        webbrowser.open = spy  # type: ignore[assignment]
        try:
            launched = auto.autostart_view()
        finally:
            webbrowser.open = original
        check(
            "V3 senza flag accende (e riusa) la stessa vista",
            launched == url,
            launched,
        )
        check(
            "V3 e non apre una finestra esterna: la pagina la mostra OpenCode",
            opened == [],
            opened,
        )
        check(
            "V3 e non e' un secondo processo",
            cli._read_up().get("pid") == record.get("pid"),
            (cli._read_up().get("pid"), record.get("pid")),
        )
    finally:
        down()

    check(
        "V4 chiusa per davvero: nessun daemon, nessun pid file",
        not cli._read_up() and not Path(cli._up_file()).exists(),
        cli._read_up(),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
