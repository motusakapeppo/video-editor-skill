"""vx.events: append-only JSONL, monotonic seq, survives a crash, no network."""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import events  # noqa: E402

PASS = 0
FAIL = 0

MODULE = ROOT / "scripts" / "vx" / "events.py"
NETWORK = ("socket", "urllib", "http.client", "requests", "httpx", "aiohttp")


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def _iso_utc(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError):
        return False
    return True


def test_sequence_and_fields(base: Path) -> None:
    path = base / "nested" / "events.jsonl"
    log = events.EventLog(path)
    check("file creato pigramente", not path.exists(), path)
    log.emit("ingest", "auto.ingest", "done")
    check("file esiste dopo il primo emit", path.is_file(), path)

    log.emit("render", "auto.render", "start")
    log.emit("render", "auto.render", "done", ms=12.5, detail="master scritto")
    records = events.read(path)
    check("tre righe lette", len(records) == 3, len(records))
    check(
        "seq da 1 e monotona",
        [r["seq"] for r in records] == [1, 2, 3],
        [r["seq"] for r in records],
    )
    check(
        "campi obbligatori presenti in ordine",
        all(list(record) == list(events.FIELDS) for record in records),
        records[0] if records else None,
    )
    check(
        "ts e' ISO8601 UTC",
        all(_iso_utc(record["ts"]) for record in records),
        records[0]["ts"],
    )
    check(
        "stage/code/status conservati",
        (records[2]["stage"], records[2]["code"], records[2]["status"])
        == ("render", "auto.render", "done"),
        records[2],
    )
    check(
        "ms e' float",
        isinstance(records[2]["ms"], float) and records[2]["ms"] == 12.5,
        records[2]["ms"],
    )
    check(
        "detail e' stringa",
        records[2]["detail"] == "master scritto",
        records[2]["detail"],
    )
    check(
        "default ms/detail",
        records[0]["ms"] == 0.0 and records[0]["detail"] == "",
        records[0],
    )

    raw = path.read_text(encoding="utf-8").splitlines()
    check(
        "una riga JSON per evento",
        len(raw) == 3 and all(json.loads(line) for line in raw),
        len(raw),
    )
    check(
        "flush per riga: il contenuto e' gia' su disco a handle aperto",
        len(events.read(path)) == 3,
        len(events.read(path)),
    )
    log.close()


def test_crash_survival(base: Path) -> None:
    path = base / "crash.jsonl"
    log = events.EventLog(path)
    log.emit("triage", "auto.triage", "done")
    log.emit("render", "auto.render", "start")
    del log  # nessun close(): il processo muore qui

    survived = events.read(path)
    check("crash: gli eventi scritti restano", len(survived) == 2, len(survived))

    resumed = events.EventLog(path)
    resumed.emit("render", "auto.render", "done")
    resumed.close()
    records = events.read(path)
    check(
        "crash: seq continua da 3, senza ripartire",
        [r["seq"] for r in records] == [1, 2, 3],
        [r["seq"] for r in records],
    )
    check("crash: nessuna riga riscritta", len(records) == 3, len(records))

    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 4, "ts": "2026-')
    check(
        "crash a meta' riga: il record troncato non e' evidenza",
        len(events.read(path)) == 3,
        len(events.read(path)),
    )
    tail = events.EventLog(path)
    tail.emit("qc", "auto.qc", "done")
    tail.close()
    resumed_records = events.read(path)
    check(
        "crash a meta' riga: il nuovo record non si incolla al troncato",
        len(resumed_records) == 4,
        len(resumed_records),
    )
    check(
        "crash a meta' riga: la seq riparte dall'ultimo record integro",
        resumed_records[-1]["seq"] == 4,
        [r["seq"] for r in resumed_records],
    )


def test_reader_edges(base: Path) -> None:
    check("read su file assente = lista vuota", events.read(base / "nope.jsonl") == [])
    empty = base / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    check("read su file vuoto = lista vuota", events.read(empty) == [])


def test_no_network() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    network = sorted(
        name
        for name in imported
        if any(name == bad or name.startswith(bad + ".") for bad in NETWORK)
    )
    check("nessun import di rete (AST)", not network, network)
    text_hits = [
        token for token in ("socket", "urllib") if re.search(rf"\b{token}\b", source)
    ]
    check("nessun riferimento a socket/urllib nel sorgente", not text_hits, text_hits)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-events-") as tmp:
        base = Path(tmp)
        test_sequence_and_fields(base)
        test_crash_survival(base)
        test_reader_edges(base)
    test_no_network()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
