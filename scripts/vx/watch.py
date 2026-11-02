"""vx.watch — aprire la vista di un lavoro mentre lo si fa.

Un solo verbo, una sola riga da leggere: ``vx auto ... --watch`` avvia un
server locale sul lavoro e stampa l'indirizzo **prima** che il render parta.
Il server e' un processo staccato: sopravvive alla fine del render, cosi' dopo
il "FATTO" il pulsante del risultato funziona ancora.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

#: Flag di Windows per non agganciare il figlio alla console del padre.
_DETACHED = 0x00000008
_NO_WINDOW = 0x08000000


def free_port() -> int:
    """Una porta libera su loopback, chiesta al sistema invece che indovinata."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def script() -> Path:
    """Il launcher ``vx`` accanto all'interprete in uso."""
    candidate = Path(sys.executable).with_name("vx.exe")
    return candidate if candidate.is_file() else Path(sys.executable)


def spawn(directory: Path, port: Optional[int] = None) -> str:
    """Avvia il server sul lavoro e restituisce l'indirizzo da aprire.

    Non solleva se il server non parte: la vista e' un aiuto, non una
    condizione del montaggio. Chi chiama riceve comunque un indirizzo e, se
    nessuno risponde, lo scopre subito.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    chosen = int(port or free_port())
    python = Path(sys.executable)
    command = [str(python)]
    launcher = python.with_name("vx.exe")
    if launcher.is_file():
        command = [str(launcher)]
    command += ["serve", str(directory), "--port", str(chosen)]
    flags = _DETACHED | _NO_WINDOW if os.name == "nt" else 0
    try:
        child = subprocess.Popen(  # noqa: S603 - comando costruito qui, niente shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except OSError:
        return ""
    (directory / "watch.pid").write_text(f"{child.pid} {chosen}\n", encoding="utf-8")
    return f"http://127.0.0.1:{chosen}/"


def running(directory: Path) -> Optional[str]:
    """L'indirizzo del server gia' avviato per questo lavoro, se e' vivo."""
    record = Path(directory) / "watch.pid"
    try:
        pid_text, port_text = record.read_text(encoding="utf-8").split()
        pid, port = int(pid_text), int(port_text)
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return None
    return f"http://127.0.0.1:{port}/"
