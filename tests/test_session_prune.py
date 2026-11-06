"""`vx session prune`: le sessioni vecchie si tolgono da un comando, non a mano.

    python tests/test_session_prune.py

Il 6 novembre 2026 c'erano 492 sessioni di prova accantonate a mano in una
cartella: nessuno le avrebbe piu' guardate, e cancellarle a mano significa
rischiare la cartella sbagliata. Qui si prova che il comando fa la cosa giusta e
che non tocca quello che non gli appartiene.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, session  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def make_session(root: Path, name: str, *, age_days: float) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({"id": name}), encoding="utf-8")
    (folder / "events.jsonl").write_text('{"type":"start"}\n', encoding="utf-8")
    stamp = time.time() - age_days * 86400
    for path in folder.rglob("*"):
        if path.is_file():
            os.utime(path, (stamp, stamp))
    os.utime(folder, (stamp, stamp))
    return folder


def test_dry_run_changes_nothing() -> None:
    print("\n1. --dry-run elenca e non cancella")
    with tempfile.TemporaryDirectory(prefix="vx-prune-") as tmp:
        root = Path(tmp)
        old = make_session(root, "20260101-000000-aaaa", age_days=10)
        make_session(root, "20260102-000000-bbbb", age_days=1)
        result = session.prune_sessions(root=root, days=7, dry_run=True)
        check("dichiara cosa rimuoverebbe", result["removed_count"] == 1, result["removed"])
        check("la cartella vecchia e' ancora li'", old.is_dir())
        check("dice che non ha cancellato", result["dry_run"] is True, result["dry_run"])


def test_removes_only_old() -> None:
    print("\n2. rimuove le vecchie e tiene le giovani")
    with tempfile.TemporaryDirectory(prefix="vx-prune-") as tmp:
        root = Path(tmp)
        old = make_session(root, "20260101-000000-aaaa", age_days=10)
        young = make_session(root, "20260102-000000-bbbb", age_days=1)
        result = session.prune_sessions(root=root, days=7)
        check("una rimossa", result["removed_count"] == 1, result["removed"])
        check("la vecchia non c'e' piu'", not old.exists())
        check("la giovane resta", young.is_dir())
        check("ha contato i byte liberati", result["freed_bytes"] > 0, result["freed_bytes"])


def test_foreign_folder_survives() -> None:
    print("\n3. una cartella che non e' una sessione resta dov'e'")
    with tempfile.TemporaryDirectory(prefix="vx-prune-") as tmp:
        root = Path(tmp)
        make_session(root, "20260101-000000-aaaa", age_days=30)
        stranger = root / "appunti"
        stranger.mkdir()
        (stranger / "note.txt").write_text("roba mia", encoding="utf-8")
        old = time.time() - 30 * 86400
        os.utime(stranger / "note.txt", (old, old))
        os.utime(stranger, (old, old))
        result = session.prune_sessions(root=root, days=7)
        check("conta la cartella estranea", result["foreign_count"] == 1, result["foreign_count"])
        check("e non la tocca", (stranger / "note.txt").is_file())


def test_real_root_needs_force() -> None:
    print("\n4. la radice vera delle sessioni chiede --force")
    with tempfile.TemporaryDirectory(prefix="vx-prune-") as tmp:
        original = session.default_root
        session.default_root = lambda: Path(tmp)  # type: ignore[assignment]
        try:
            make_session(Path(tmp), "20260101-000000-aaaa", age_days=30)
            raised = None
            try:
                session.prune_sessions(days=7)
            except contract.VxError as exc:
                raised = str(exc)
            check("rifiuta senza --force", raised is not None and "--force" in raised, raised)
            kept = session.prune_sessions(days=7, force=True)
            check("con --force procede", kept["removed_count"] == 1, kept["removed"])
        finally:
            session.default_root = original  # type: ignore[assignment]


def test_missing_root_is_declared() -> None:
    print("\n5. una cartella inesistente non esplode")
    with tempfile.TemporaryDirectory(prefix="vx-prune-") as tmp:
        result = session.prune_sessions(root=Path(tmp) / "non-esiste", days=7)
        check("zero rimosse", result["removed_count"] == 0, result["removed"])
        check("zero tenute", result["kept_count"] == 0, result["kept_count"])


def main() -> int:
    test_dry_run_changes_nothing()
    test_removes_only_old()
    test_foreign_folder_survives()
    test_real_root_needs_force()
    test_missing_root_is_declared()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
