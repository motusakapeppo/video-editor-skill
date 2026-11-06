"""vx.livepub — i fotogrammi pubblicati mentre il motore costruisce.

La vista non deve aspettare la fine: quando un motore grafico (Chromium, o
ffmpeg per un pezzo veloce) rasterizza un fotogramma, quel fotogramma finisce in
``<workdir>/live/<motore>/`` con una riga nel manifesto, e la pagina lo mostra
subito — con l'indice, il tempo virtuale, i millisecondi e l'hash.

Tre regole, perché "tempo reale" non diventi "disco pieno":

- **campionamento**: non si pubblica ogni fotogramma (300 PNG da 1080x1920 sono
  centinaia di MB), ma uno ogni ``stride``, e in miniatura;
- **finestra**: si tengono gli ultimi ``keep`` fotogrammi (anello); i più vecchi
  si tolgono insieme alla loro riga, così manifesto e file non divergono mai;
- **atomicità**: il manifesto si scrive ``tmp -> os.replace``, quindi la pagina
  vede la versione prima o quella dopo, mai un JSON troncato.

Chi legge è :mod:`vx.serve`; chi scrive è il renderer, che chiama
:func:`publish` mentre lavora.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

__all__ = ["SCHEMA", "ENGINES", "publish", "read_manifest", "reset"]

SCHEMA = "vx.live/1"

#: I motori che possono pubblicare. Un nome fuori di qui è un errore, non un
#: motore nuovo: la vista deve sapere cosa sta guardando.
ENGINES = ("chromium", "ffmpeg", "pillow")

MANIFEST_NAME = "manifest.json"


def _root(workdir: Any, engine: str) -> Path:
    if engine not in ENGINES:
        raise ValueError(f"motore sconosciuto: {engine!r} (attesi: {', '.join(ENGINES)})")
    return Path(str(workdir)) / "live" / engine


def _write_atomic(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_manifest(workdir: Any, engine: str) -> Optional[Dict[str, Any]]:
    """Il manifesto corrente, o ``None`` se il motore non ha ancora pubblicato."""
    target = _root(workdir, engine) / MANIFEST_NAME
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def reset(workdir: Any, engine: str) -> None:
    """Svuota la finestra di un motore: si chiama all'inizio di un lavoro."""
    root = _root(workdir, engine)
    if not root.is_dir():
        return
    for item in root.glob("frame_*"):
        try:
            item.unlink()
        except OSError:
            continue
    try:
        (root / MANIFEST_NAME).unlink()
    except OSError:
        pass


def publish(
    workdir: Any,
    *,
    engine: str,
    frame_index: int,
    t: float,
    image: bytes,
    total: Optional[int] = None,
    stride: int = 1,
    keep: int = 40,
    suffix: str = ".jpg",
) -> Dict[str, Any]:
    """Pubblica un fotogramma e aggiorna il manifesto; ritorna la riga scritta.

    ``image`` sono i byte già codificati (la miniatura la fa il chiamante). Il
    tempo ``t`` è quello **del montaggio**, non del muro: è ciò che rende la
    striscia leggibile ("a 4,2 s era qui").
    """
    root = _root(workdir, engine)
    root.mkdir(parents=True, exist_ok=True)
    index = int(frame_index)
    name = f"frame_{index:06d}{suffix}"
    payload = image if isinstance(image, (bytes, bytearray)) else bytes(image)
    _write_atomic(root / name, bytes(payload))

    manifest = read_manifest(workdir, engine) or {
        "schema": SCHEMA,
        "engine": engine,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "frames": [],
    }
    rows: List[Dict[str, Any]] = list(manifest.get("frames") or [])
    row = {
        "i": index,
        "t": round(float(t), 3),
        "file": name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(bytes(payload)).hexdigest(),
    }
    rows = [item for item in rows if int(item.get("i", -1)) != index]
    rows.append(row)
    rows.sort(key=lambda item: int(item.get("i", 0)))

    window = max(1, int(keep))
    dropped = rows[:-window] if len(rows) > window else []
    rows = rows[-window:]
    for item in dropped:
        try:
            (root / str(item.get("file"))).unlink()
        except OSError:
            continue

    manifest.update(
        {
            "schema": SCHEMA,
            "engine": engine,
            "stride": max(1, int(stride)),
            "keep": window,
            "total": int(total) if total is not None else manifest.get("total"),
            "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "frames": rows,
            "last": row,
        }
    )
    _write_atomic(
        root / MANIFEST_NAME,
        json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"),
    )
    return row
