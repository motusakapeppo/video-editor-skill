"""vx.history: append-only EDL journal, checkpoints, undo, structural diff.

python tests/test_history.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import history  # noqa: E402

PASS = 0
FAIL = 0

MODULE = ROOT / "scripts" / "vx" / "history.py"
NETWORK = ("socket", "urllib", "http.client", "requests", "httpx", "aiohttp")


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:60} {detail}")


def _sha(document: Dict[str, Any]) -> str:
    """Recompute the digest the way the contract says: canonical JSON."""
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _doc(step: int, *, duration: float = 3.0, extra_track: bool = False) -> dict:
    video: List[dict] = [{"clip_id": "c1", "duration_sec": duration}]
    if extra_track:
        video.append({"clip_id": "c2", "duration_sec": 1.5})
    return {
        "contract_version": "vx.edl/1.0",
        "timeline": {"fps": 30.0, "duration_sec": duration},
        "tracks": {"video": video, "audio": []},
        "step": step,
    }


def _iso_utc(value: object) -> bool:
    try:
        datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError):
        return False
    return True


def _journal(base: Path, name: str, count: int = 5) -> Path:
    path = base / name
    for step in range(1, count + 1):
        history.append(path, _doc(step), note=f"decision {step}")
    return path


def test_five_appends(base: Path) -> None:
    path = _journal(base, "five.jsonl")
    records = history.read(path)
    check("cinque append -> cinque record", len(records) == 5, len(records))
    check(
        "seq da 1 a 5, monotona",
        [record["seq"] for record in records] == [1, 2, 3, 4, 5],
        [record["seq"] for record in records],
    )
    check(
        "campi obbligatori presenti in ordine",
        all(list(record) == list(history.FIELDS) for record in records),
        list(records[0]) if records else None,
    )
    check(
        "kind/note conservati",
        (records[0]["kind"], records[0]["note"]) == ("plan", "decision 1"),
        (records[0]["kind"], records[0]["note"]),
    )
    check(
        "ts e' ISO8601 UTC",
        all(_iso_utc(record["ts"]) for record in records),
        records[0]["ts"],
    )
    check(
        "ogni sha256 si ricalcola dal documento",
        all(_sha(record["document"]) == record["sha256"] for record in records),
        records[0]["sha256"],
    )
    check(
        "current e' il quinto documento",
        history.current(path) == _doc(5),
        history.current(path),
    )
    raw = path.read_text(encoding="utf-8").splitlines()
    check(
        "una riga JSON per record",
        len(raw) == 5 and all(isinstance(json.loads(line), dict) for line in raw),
        len(raw),
    )
    check(
        "read_checked: nessun danneggiato",
        history.read_checked(path)[1] == [],
        history.read_checked(path)[1],
    )

    # Canonicalisation: key order and non-ASCII must not move the digest.
    digest = history.append(path, {"b": 1, "a": "citta'"})["sha256"]
    other = history.append(path, {"a": "citta'", "b": 1})["sha256"]
    check("sha canonico: ordine chiavi irrilevante", digest == other, digest[:12])
    check(
        "seq continua dopo il riapri",
        history.read(path)[-1]["seq"] == 7,
        [r["seq"] for r in history.read(path)],
    )


def test_undo_steps(base: Path) -> None:
    path = _journal(base, "undo.jsonl")
    restored = history.undo(path, steps=2)
    check(
        "undo(2) restituisce il terzo documento",
        restored["document"] == _doc(3),
        restored["document"].get("step"),
    )
    check(
        "undo scrive un record restore", restored["kind"] == "restore", restored["kind"]
    )
    records = history.read(path)
    check("il journal ha ora 6 record", len(records) == 6, len(records))
    check(
        "il restore e' l'ultimo", records[-1]["kind"] == "restore", records[-1]["kind"]
    )
    check(
        "current e' il documento ripristinato",
        history.current(path) == _doc(3),
        history.current(path),
    )
    check(
        "sha del restore si ricalcola",
        _sha(restored["document"]) == restored["sha256"],
        restored["sha256"],
    )

    second = history.undo(path, steps=1)
    check(
        "secondo undo(1) torna al secondo, non al ripristinato",
        second["document"] == _doc(2),
        second["document"].get("step"),
    )
    check(
        "restore non consuma uno step: sono 7 record",
        len(history.read(path)) == 7,
        len(history.read(path)),
    )


def test_undo_clamp(base: Path) -> None:
    path = _journal(base, "clamp.jsonl")
    clamped = history.undo(path, steps=99)
    check(
        "undo(99) si ferma al primo documento",
        clamped["document"] == _doc(1),
        clamped["document"].get("step"),
    )
    check(
        "la nota dice clamped_to_oldest",
        "clamped_to_oldest" in clamped["note"],
        clamped["note"],
    )
    check("clamp: kind restore", clamped["kind"] == "restore", clamped["kind"])
    check(
        "clamp: current e' il primo",
        history.current(path) == _doc(1),
        history.current(path),
    )
    again = history.undo(path, steps=5)
    check(
        "clamp di nuovo sullo stesso punto non lancia",
        again["document"] == _doc(1) and "clamped_to_oldest" in again["note"],
        again["note"],
    )


def test_tampered_line(base: Path) -> None:
    path = _journal(base, "tamper.jsonl", count=3)
    lines = path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[1])
    forged["document"]["step"] = 999
    lines[1] = json.dumps(forged, ensure_ascii=False, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records, damaged = history.read_checked(path)
    check("riga manomessa: 2 record validi", len(records) == 2, len(records))
    check("riga manomessa: segnalata in damaged", len(damaged) == 1, len(damaged))
    check(
        "riga manomessa: motivo sha256_mismatch",
        bool(damaged) and damaged[0]["reason"] == "sha256_mismatch",
        damaged[0]["reason"] if damaged else None,
    )
    check(
        "riga manomessa: linea 2",
        bool(damaged) and damaged[0]["line"] == 2,
        damaged[0]["line"] if damaged else None,
    )
    check(
        "riga manomessa: read la salta",
        [record["document"]["step"] for record in history.read(path)] == [1, 3],
        [record["document"]["step"] for record in history.read(path)],
    )
    check(
        "riga manomessa: current salta il falso",
        history.current(path) == _doc(3),
        history.current(path),
    )
    check(
        "riga manomessa: read() == read_checked()[0]",
        history.read(path) == records,
        None,
    )


def test_torn_last_line(base: Path) -> None:
    path = _journal(base, "torn.jsonl", count=2)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 3, "ts": "2026-')
    check(
        "riga troncata: i record integri restano",
        len(history.read(path)) == 2,
        len(history.read(path)),
    )
    check(
        "riga troncata: non e' un record danneggiato",
        history.read_checked(path)[1] == [],
        history.read_checked(path)[1],
    )

    written = history.append(path, _doc(3), note="dopo il crash")
    check(
        "append dopo il troncato: seq riprende da 3",
        written["seq"] == 3,
        written["seq"],
    )
    records = history.read(path)
    check("append dopo il troncato: 3 record validi", len(records) == 3, len(records))
    check(
        "append dopo il troncato: seq 1,2,3",
        [record["seq"] for record in records] == [1, 2, 3],
        [record["seq"] for record in records],
    )
    check(
        "append dopo il troncato: current e' il nuovo",
        history.current(path) == _doc(3),
        history.current(path),
    )
    check(
        "append dopo il troncato: l'ultima riga e' intera",
        path.read_text(encoding="utf-8").endswith("\n"),
        None,
    )


def test_checkpoint_roundtrip(base: Path) -> None:
    path = base / "checkpoint.jsonl"
    history.append(path, _doc(1), note="prima")
    saved = history.checkpoint(path, "prima-del-crop")
    check("checkpoint: kind checkpoint", saved["kind"] == "checkpoint", saved["kind"])
    check(
        "checkpoint: label nella note", saved["note"] == "prima-del-crop", saved["note"]
    )
    check(
        "checkpoint: porta il documento corrente",
        saved["document"] == _doc(1),
        saved["document"].get("step"),
    )
    history.append(path, _doc(2, duration=7.5), note="dopo")
    check(
        "current e' cambiato",
        history.current(path) == _doc(2, duration=7.5),
        history.current(path),
    )
    found = history.at(path, "prima-del-crop")
    check(
        "at(label) ritrova il documento",
        found == _doc(1),
        found.get("step") if found else None,
    )
    check(
        "at(label sconosciuta) = None",
        history.at(path, "mai-visto") is None,
        history.at(path, "mai-visto"),
    )
    history.checkpoint(path, "prima-del-crop")
    latest = history.at(path, "prima-del-crop")
    check(
        "at(label) vince l'ultimo checkpoint con quella label",
        latest == _doc(2, duration=7.5),
        latest.get("step") if latest else None,
    )


def test_diff(base: Path) -> None:
    before = _doc(1, duration=3.0)
    after = _doc(1, duration=4.5, extra_track=True)
    result = history.diff(before, after)
    check(
        "diff: durata cambiata come json path",
        "tracks.video[0].duration_sec" in result["changed"],
        result["changed"],
    )
    check(
        "diff: timeline.duration_sec cambiata",
        "timeline.duration_sec" in result["changed"],
        result["changed"],
    )
    check(
        "diff: traccia aggiunta in added",
        "tracks.video[1]" in result["added"],
        result["added"],
    )
    check("diff: nessun removed", result["removed"] == [], result["removed"])
    check(
        "diff: la lista che ricorre non finisce in changed",
        "tracks.video" not in result["changed"],
        result["changed"],
    )
    check(
        "diff: step non cambia e non compare",
        "step" not in result["changed"],
        result["changed"],
    )

    left = {"tracks": {"video": [{"clip_id": "c1"}], "audio": []}, "keep": 1}
    right = {"tracks": {"video": [{"clip_id": "c1"}], "audio": [], "keep": 2}}
    moved = history.diff(left, right)
    check(
        "diff: chiave rimossa al livello giusto",
        moved["removed"] == ["keep"],
        moved["removed"],
    )
    check(
        "diff: chiave annidata aggiunta in added",
        moved["added"] == ["tracks.keep"],
        moved["added"],
    )

    empty = history.diff(before, before)
    check(
        "diff identico: tre liste vuote",
        empty == {"changed": [], "added": [], "removed": []},
        empty,
    )


def test_no_network() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    hits = sorted(
        name
        for name in imported
        if any(name == bad or name.startswith(bad + ".") for bad in NETWORK)
    )
    check("nessun import di rete (AST)", not hits, hits)
    words = [
        token
        for token in ("socket", "urllib", "requests")
        if re.search(rf"\b{token}\b", source)
    ]
    check("nessun riferimento testuale a socket/urllib/requests", not words, words)
    check("nessun type: ignore nel sorgente", "# type: ignore" not in source, None)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-history-") as tmp:
        base = Path(tmp)
        test_five_appends(base)
        test_undo_steps(base)
        test_undo_clamp(base)
        test_tampered_line(base)
        test_torn_last_line(base)
        test_checkpoint_roundtrip(base)
        test_diff(base)
    test_no_network()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
