"""Il vault e' la regola: l'engine non cancella cio' che non ha creato.

python tests/test_vault.py
"""

from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import vault  # noqa: E402
from vx.contract import VxError  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:52} {reason}")


def raises_vx(fn) -> VxError | None:
    try:
        fn()
    except VxError as exc:
        return exc
    return None


def write(path: Path, data: bytes = b"owned-bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_manifest(tmp: Path) -> None:
    root = tmp / "happy"
    write(root / "clip.mp4", b"AAAA")
    resolved = vault.declare(root)
    check("declare creates .vx-owned at root", (root / ".vx-owned").is_file())
    check("declare returns resolved root", resolved == root.resolve())
    record = vault.register(root, "clip.mp4")
    check(
        "register returns record with sha256",
        len(record["sha256"]) == 64,
        record["path"],
    )
    check("register kind defaults to artifact", record["kind"] == "artifact")
    check("is_owned true for relative path", vault.is_owned(root, "clip.mp4"))
    check("is_owned true for absolute path", vault.is_owned(root, root / "clip.mp4"))
    check(
        "is_owned false for unregistered sibling", not vault.is_owned(root, "other.mp4")
    )
    entries = vault.owned_entries(root)
    check("owned_entries lists the registered file", "clip.mp4" in entries)
    manifest = json.loads((root / ".vx-owned").read_text(encoding="utf-8"))
    check(
        "manifest shape schema/root/entries",
        set(manifest) == {"schema", "root", "created_at", "entries"},
        str(sorted(manifest)),
    )
    check(
        "manifest entry records size",
        manifest["entries"]["clip.mp4"]["size"] == 4,
    )


def test_unowned_refused(tmp: Path) -> None:
    root = tmp / "unowned"
    vault.declare(root)
    user_file = write(root / "utente.mov", b"mine")
    error = raises_vx(lambda: vault.remove(root, "utente.mov"))
    check("remove refuses an unowned file", error is not None, str(error))
    check("refused file still exists", user_file.is_file())


def test_traversal_refused(tmp: Path) -> None:
    root = tmp / "escape"
    vault.declare(root)
    outside = write(tmp / "escape-outside.txt", b"outside")
    error = raises_vx(lambda: vault.remove(root, "../escape-outside.txt"))
    check("remove refuses '..' escaping root", error is not None, str(error))
    error = raises_vx(lambda: vault.remove(root, outside))
    check("remove refuses absolute outside path", error is not None, str(error))
    check("outside file untouched", outside.is_file())
    error = raises_vx(lambda: vault.register(root, outside))
    check("register refuses absolute outside path", error is not None, str(error))
    error = raises_vx(lambda: vault.resolve_inside(root, "../x"))
    check("resolve_inside rejects '..'", error is not None)


def test_symlink_refused(tmp: Path) -> None:
    root = tmp / "links"
    vault.declare(root)
    outside = write(tmp / "link-target.txt", b"target")
    link = root / "puntatore.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        skip("symlink pointing outside refused", f"os.symlink non permesso: {exc}")
        return
    error = raises_vx(lambda: vault.remove(root, "puntatore.txt"))
    check("remove refuses symlink to outside", error is not None, str(error))
    check("symlink itself untouched", link.is_symlink())
    check("symlink target untouched", outside.is_file())


def test_quarantine_move(tmp: Path) -> None:
    root = tmp / "quarantine"
    payload = write(root / "out" / "render.mp4", b"RENDER")
    vault.register(root, "out/render.mp4")
    destination = vault.remove(root, "out/render.mp4")
    check(
        "remove reports a quarantine destination",
        destination.is_file(),
        str(destination),
    )
    # `%TEMP%` sui runner Windows e' un percorso 8.3 (`RUNNER~1`): senza
    # risolvere, `relative_to` confronta stringhe diverse e solleva ValueError.
    rel = destination.resolve().relative_to(root.resolve()).as_posix()
    check(
        "destination lives under .quarantine/<date>/",
        rel.startswith(".quarantine/"),
        rel,
    )
    check("original path is gone", not payload.exists())
    check("quarantined bytes preserved", destination.read_bytes() == b"RENDER")
    check("ownership dropped after move", not vault.is_owned(root, "out/render.mp4"))
    journal = root / ".quarantine" / vault.JOURNAL_NAME
    check(
        "move recorded in journal",
        journal.is_file() and "render.mp4" in journal.read_text(encoding="utf-8"),
    )


def test_purge(tmp: Path) -> None:
    root = tmp / "purge"
    write(root / "old.bin", b"OLD")
    vault.register(root, "old.bin")
    destination = vault.remove(root, "old.bin")
    old_stamp = time.time() - 30 * 86400
    os.utime(destination, (old_stamp, old_stamp))

    dry = vault.purge(root, days=7)
    check("purge defaults to dry_run True", dry["dry_run"] is True)
    check("dry run lists one candidate", dry["count"] == 1, str(dry["candidates"]))
    check("dry run deleted nothing", destination.is_file())
    check("candidate carries age_days", dry["candidates"][0]["age_days"] >= 29)
    nothing = vault.purge(root, days=60, dry_run=False)
    check(
        "purge without age match deletes nothing",
        nothing["deleted"] == [],
        str(nothing),
    )
    check("file survives young purge", destination.is_file())

    os.utime(destination, (old_stamp, old_stamp))
    purged = vault.purge(root, days=0, dry_run=False)
    check("purge days=0 deletes the old entry", len(purged["deleted"]) == 1)
    check("purged file is gone", not destination.exists())


def test_directory_guard(tmp: Path) -> None:
    root = tmp / "directory"
    write(root / "pack" / "a.txt", b"A")
    write(root / "pack" / "b.txt", b"B")
    vault.register(root, "pack/a.txt")
    vault.register(root, "pack/b.txt")
    intruder = write(root / "pack" / "utente.txt", b"USER")
    error = raises_vx(lambda: vault.remove(root, "pack"))
    check("directory with one unowned file refused", error is not None, str(error))
    check("directory untouched after refusal", (root / "pack" / "a.txt").is_file())
    check("intruder untouched", intruder.is_file())

    intruder.unlink()
    destination = vault.remove(root, "pack")
    check(
        "fully owned directory moved",
        destination.is_dir() and destination.name == "pack",
    )
    check("owned directory gone from old path", not (root / "pack").exists())
    check("quarantined directory keeps contents", (destination / "a.txt").is_file())

    empty = root / "vuota"
    empty.mkdir()
    error = raises_vx(lambda: vault.remove(root, "vuota"))
    check("empty directory refused (no owned files)", error is not None, str(error))
    check("empty directory untouched", empty.is_dir())


def test_verify(tmp: Path) -> None:
    root = tmp / "verify"
    target = write(root / "verify.bin", b"0123456789")
    record = vault.register(root, "verify.bin")
    clean = vault.verify(root)
    check("verify clean run ok", clean["ok"] is True, str(clean))
    check("verify checked one entry", clean["checked"] == 1)
    raw = bytearray(target.read_bytes())
    raw[0] = ord("9")
    target.write_bytes(bytes(raw))
    dirty = vault.verify(root)
    check("verify detects flipped byte", dirty["ok"] is False)
    check(
        "changed names the file",
        dirty["changed"] == ["verify.bin"],
        str(dirty["changed"]),
    )
    check("changed file is not touched", target.read_bytes() == b"9123456789")

    write(root / "utente-extra.txt", b"USER")
    extra = vault.verify(root)
    check("verify lists unowned file", "utente-extra.txt" in extra["unowned_on_disk"])
    check("verify does not touch unowned file", (root / "utente-extra.txt").is_file())
    check("verify still reports the owned change", extra["changed"] == ["verify.bin"])
    repaired = vault.verify(root, repair_manifest=True)
    check(
        "repair adopts the new hash",
        vault.verify(root)["ok"] is True and repaired["ok"] is True,
    )
    new_hash = vault.owned_entries(root)["verify.bin"]["sha256"]
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    check(
        "repair rewrote the stale hash",
        new_hash == actual and new_hash != record["sha256"],
        new_hash[:12],
    )


def test_verify_original(tmp: Path) -> None:
    target = write(tmp / "original.bin", b"canonico")
    sha = vault.verify_original(target, hashlib.sha256(b"canonico").hexdigest())
    check("verify_original true on match", sha is True)
    error = raises_vx(lambda: vault.verify_original(target, "0" * 64))
    check("verify_original raises on mismatch", error is not None, str(error))
    payload = getattr(error, "payload", {})
    check(
        "mismatch payload carries expected/actual",
        payload.get("expected") == "0" * 64
        and payload.get("actual") != payload.get("expected"),
        str(payload),
    )


def test_space(tmp: Path) -> None:
    probe = tmp / "space"
    probe.mkdir()
    error = raises_vx(
        lambda: vault.ensure_space(probe, need_bytes=10**15, headroom_ratio=1.5)
    )
    check("ensure_space raises for absurd need", error is not None, str(error))
    payload = getattr(error, "payload", {})
    check(
        "space payload has the three numbers",
        set(payload) == {"free_bytes", "need_bytes", "required_bytes"},
        str(sorted(payload)),
    )
    check(
        "space payload required = need * headroom",
        payload.get("required_bytes") == 10**15 * 3 // 2,
        str(payload.get("required_bytes")),
    )
    ok = vault.ensure_space(probe, need_bytes=1024)
    check(
        "ensure_space ok for tiny need",
        ok["ok"] is True and ok["required_bytes"] == 1536,
    )
    check("ensure_space reports free bytes", ok["free_bytes"] > 0)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-vault-") as tmp_name:
        tmp = Path(tmp_name)
        test_manifest(tmp)
        test_unowned_refused(tmp)
        test_traversal_refused(tmp)
        test_symlink_refused(tmp)
        test_quarantine_move(tmp)
        test_purge(tmp)
        test_directory_guard(tmp)
        test_verify(tmp)
        test_verify_original(tmp)
        test_space(tmp)
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
