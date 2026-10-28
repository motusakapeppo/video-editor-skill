"""F17b -- do the originals survive because the operating system stops the write?

F17-T08 asks for originals guarded by the OS itself: the read-only attribute
*and* a Windows ACL, each proven separately by a write that actually fails.
This file asks the production code what it applies, then measures the result.

What the code applies today (read, not assumed):

* ``scripts/vx/vault.py:678`` ``verify_original()`` re-hashes a source and
  raises when the digest moved -- detection *after* the bytes changed.
* ``scripts/vx/vault.py:246`` ``register()`` and ``:138`` ``_manifest_is_safe``
  keep the ownership manifest; ``:363`` ``remove()`` refuses anything unowned
  and moves the rest into quarantine instead of unlinking.
* ``scripts/vx/winpath.py:46`` ``source_fingerprint()`` reads the source in
  ``"rb"`` and remembers size/mtime/hash; ``scripts/vx/vault.py:117`` does the
  same while hashing. A read handle protects nothing after it closes.
* ``scripts/vx/ingest.py`` re-encodes into a work directory, it never copies a
  source; the only ``shutil.copy2`` calls are in ``scripts/vx/deliverables.py``
  (outputs) and ``scripts/vx/gates.py`` (a test fixture copy).

So the code has no OS-level guard: zero occurrences of FILE_ATTRIBUTE_READONLY,
SetFileAttributes, chmod, icacls or SetNamedSecurityInfo under ``scripts/vx``.
Section 1 fails on exactly that, naming the mechanism and the file:line. The
test does not fix production code; it reports.

To keep the failures honest, section 4 is a *harness control*: the same probes,
pointed at a file this test protects by hand, must observe the block. If they
did not, a failure in sections 2-3 would mean nothing. The control is labelled
and never presented as proof about production code.

Run:
    python tests/test_f17_acl.py

Exit code 0 only when every check passed and the suite stayed under
CEILING_SECONDS. On a host that is not Windows the Windows-only sections skip
with their reason and the suite still exits 0.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from vx import artifacts, vault, winpath  # noqa: E402
from vx.contract import VxError  # noqa: E402

CEILING_SECONDS = 60.0
IS_WINDOWS = os.name == "nt"

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

# The acceptance wording, kept in one place so every failure names the same
# missing mechanism and the same place in the code.
DEFECT_MECHANISM = "FILE_ATTRIBUTE_READONLY + deny-write ACL"
DEFECT_SITE = "scripts/vx/vault.py:678 verify_original (after-the-fact hash check)"

PROTECT_HINTS = (
    "protect",
    "readonly",
    "read_only",
    "seal",
    "freeze",
    "immutable",
    "harden",
    "deny_write",
)
MECHANISM_TOKENS = (
    "file_attribute_readonly",
    "setfileattributes",
    "chmod",
    "icacls",
    "setnamedsecurityinfo",
    "deny_access",
    "win32security",
)

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    mark = "PASS" if ok else "FAIL"
    print(f"    [{mark}] {label:52} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:52} {reason}")


def note(text: str) -> None:
    print(f"    [note] {text}")


# ---------------------------------------------------------------------------
# Windows primitives: attributes through ctypes, ACL through icacls
# ---------------------------------------------------------------------------
_ATTRIBUTE_API = None


def _attribute_api():
    """(GetFileAttributesW, SetFileAttributesW) with explicit signatures."""
    import ctypes
    from ctypes import wintypes

    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attr = lib.GetFileAttributesW
    get_attr.argtypes = [wintypes.LPCWSTR]
    get_attr.restype = wintypes.DWORD
    set_attr = lib.SetFileAttributesW
    set_attr.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    set_attr.restype = wintypes.BOOL
    return get_attr, set_attr


def get_attributes(path: Path) -> Optional[int]:
    """The raw attribute mask, or None when GetFileAttributes fails."""
    global _ATTRIBUTE_API
    if _ATTRIBUTE_API is None:
        _ATTRIBUTE_API = _attribute_api()
    mask = int(_ATTRIBUTE_API[0](str(path)))
    return None if mask == INVALID_FILE_ATTRIBUTES else mask


def set_attributes(path: Path, mask: int) -> bool:
    global _ATTRIBUTE_API
    if _ATTRIBUTE_API is None:
        _ATTRIBUTE_API = _attribute_api()
    return bool(_ATTRIBUTE_API[1](str(path), int(mask)))


def run_command(args: List[str]) -> Tuple[int, str]:
    """Run a helper command, return (exit code, stdout+stderr)."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, errors="replace"
        )
    except FileNotFoundError as exc:
        return 127, f"{args[0]} not found: {exc}"
    except OSError as exc:
        return 126, f"{args[0]} could not run: {exc}"
    merged = (proc.stdout or "") + (proc.stderr or "")
    return int(proc.returncode), merged.strip()


def first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else "<no output>"


def current_sid() -> Optional[str]:
    """The current user's SID; icacls resolves account names loosely on some hosts."""
    code, text = run_command(["whoami", "/user", "/fo", "csv", "/nh"])
    if code != 0:
        return None
    for token in text.replace('"', " ").replace(",", " ").split():
        if token.startswith("S-1-"):
            return token
    return None


def write_attempt(path: Path) -> Tuple[bool, str]:
    """Append one byte. True when it landed, False with the exception named."""
    try:
        with path.open("ab") as handle:
            handle.write(b"x")
    except PermissionError as exc:
        return False, f"PermissionError: {exc.strerror or exc}"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "write succeeded"


CHILD_PROBE = (
    "import sys\n"
    "with open(sys.argv[1], 'ab') as handle:\n"
    "    handle.write(b'x')\n"
)


def child_write_attempt(path: Path) -> Tuple[bool, str]:
    """Same write from a fresh interpreter: an OS guard must stop it as well."""
    code, text = run_command([sys.executable, "-c", CHILD_PROBE, str(path)])
    denied = code != 0 and "PermissionError" in text
    detail = "PermissionError (child process)" if denied else first_line(text)
    return denied, f"rc={code} {detail}"


# ---------------------------------------------------------------------------
# what production code offers as "protection"
# ---------------------------------------------------------------------------
def find_protection_callables():
    """Public callables in vault/artifacts/winpath whose name promises a guard."""
    found = []
    for module in (vault, artifacts, winpath):
        for name in sorted(dir(module)):
            if name.startswith("_"):
                continue
            value = getattr(module, name)
            if isinstance(value, type) or not callable(value):
                continue
            lowered = name.lower()
            if any(hint in lowered for hint in PROTECT_HINTS):
                found.append((module.__name__, name, value))
    return found


def apply_code_protection(target: Path) -> str:
    """Invoke the production guard if there is one; say what happened."""
    for module_name, func_name, func in find_protection_callables():
        for argument in (target, str(target)):
            try:
                func(argument)
            except TypeError:
                continue
            except Exception as exc:  # a guard that throws is not a guard
                return f"{module_name}.{func_name} raised {type(exc).__name__}"
            return f"{module_name}.{func_name}({type(argument).__name__})"
    return "none"


def scan_for_os_mechanisms() -> List[str]:
    """Every place under scripts/vx that names an OS-level guard, with line."""
    hits: List[str] = []
    for path in sorted((SCRIPTS / "vx").glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            for token in MECHANISM_TOKENS:
                if token in lowered:
                    relative = path.relative_to(ROOT).as_posix()
                    hits.append(f"{relative}:{number}: {token}")
    return hits


# ---------------------------------------------------------------------------
# 1. the mechanism the code actually applies
# ---------------------------------------------------------------------------
def check_code_mechanism(tmp: Path) -> None:
    print("\n1. the protection the code applies today")
    original = tmp / "original.bin"
    original.write_bytes(b"canonical-bytes")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()

    accepted = vault.verify_original(original, digest)
    check(
        "verify_original accepts an untouched original",
        accepted is True,
        "sha256 matches",
    )

    damaged = bytearray(original.read_bytes())
    damaged[0] ^= 0xFF
    original.write_bytes(bytes(damaged))
    error = None
    try:
        vault.verify_original(original, digest)
    except VxError as exc:
        error = exc
    payload = getattr(error, "payload", {}) or {}
    actual = str(payload.get("actual", ""))
    check(
        "verify_original flags one flipped byte",
        error is not None and actual == hashlib.sha256(original.read_bytes()).hexdigest(),
        f"VxError payload actual={actual[:12]}..." if actual else "no VxError raised",
    )

    guard_root = tmp / "guard"
    guard_root.mkdir()
    (guard_root / "clip.bin").write_bytes(b"ours")
    foreign = guard_root / "foreign.bin"
    foreign.write_bytes(b"not ours")
    vault.declare(guard_root)
    vault.register(guard_root, "clip.bin")
    refusal = None
    try:
        vault.remove(guard_root, "foreign.bin")
    except VxError as exc:
        refusal = exc
    check(
        "delete guard refuses an unowned file",
        refusal is not None and foreign.is_file(),
        str(refusal) if refusal is not None else "remove() accepted it",
    )

    callables = find_protection_callables()
    hits = scan_for_os_mechanisms()
    note(f"production protection callables found: {len(callables)}")
    if hits:
        for hit in hits[:8]:
            note(f"  OS mechanism token at {hit}")
    else:
        note(
            "source scan: zero occurrences of FILE_ATTRIBUTE_READONLY / "
            "SetFileAttributes / chmod / icacls in scripts/vx"
        )
    note("the code above detects a changed source; it does not prevent the write")

    check(
        "the original is guarded by the OS (attribute or ACL)",
        bool(hits) and bool(callables),
        f"missing mechanism: {DEFECT_MECHANISM}; only hash detection at {DEFECT_SITE}",
    )


# ---------------------------------------------------------------------------
# 2. read-only attribute, measured on the file the code protects
# ---------------------------------------------------------------------------
def check_readonly_attribute(tmp: Path) -> None:
    print("\n2. read-only attribute and a real write attempt")
    target = tmp / "readonly_target.bin"
    target.write_bytes(b"original-bytes")

    used = apply_code_protection(target)
    mask = get_attributes(target)
    is_readonly = mask is not None and bool(mask & FILE_ATTRIBUTE_READONLY)
    check(
        "FILE_ATTRIBUTE_READONLY is set on the protected file",
        is_readonly,
        (
            f"mask=0x{mask:03X} after {used}; missing mechanism: {DEFECT_MECHANISM}"
            if mask is not None
            else "GetFileAttributes failed"
        ),
    )

    attrib_code, attrib_text = run_command(["attrib", str(target)])
    note(f"attrib -> rc={attrib_code} {first_line(attrib_text)}")

    wrote, detail = write_attempt(target)
    check(
        "a write to the protected original raises PermissionError",
        (not wrote) and "PermissionError" in detail,
        f"{detail}; the OS was not asked to guard it ({DEFECT_SITE})"
        if wrote
        else detail,
    )


# ---------------------------------------------------------------------------
# 3. deny-write ACL, proven with the attribute removed
# ---------------------------------------------------------------------------
def check_deny_acl(tmp: Path) -> None:
    print("\n3. deny-write ACL (icacls) with the attribute removed")
    target = tmp / "acl_target.bin"
    target.write_bytes(b"original-bytes")

    used = apply_code_protection(target)
    mask = get_attributes(target)
    if mask is not None and mask & FILE_ATTRIBUTE_READONLY:
        # Requirement: the ACL must stand alone, without the attribute's help.
        set_attributes(target, mask & ~FILE_ATTRIBUTE_READONLY)
        note("read-only attribute cleared so only the ACL can act")
        mask = get_attributes(target)

    code, text = run_command(["icacls", str(target)])
    denies = "(DENY)" in text.upper()
    check(
        "a deny-write ACE for the current user is in place",
        denies,
        f"icacls rc={code} mask=0x{(mask or 0):03X} {first_line(text)}; "
        f"missing mechanism: {DEFECT_MECHANISM} (after {used})",
    )

    wrote, detail = write_attempt(target)
    check(
        "a write fails with PermissionError after the attribute is removed",
        (not wrote) and "PermissionError" in detail,
        f"{detail}; no ACE to deny it ({DEFECT_SITE})" if wrote else detail,
    )


# ---------------------------------------------------------------------------
# 4. harness control: the same probes must catch a guard this test installs
# ---------------------------------------------------------------------------
def check_harness_control(tmp: Path) -> None:
    print("\n4. harness control (not a production proof)")
    target = tmp / "control.bin"
    target.write_bytes(b"original-bytes")

    code, text = run_command(["attrib", "+R", str(target)])
    mask = get_attributes(target) or 0
    wrote, detail = write_attempt(target)
    check(
        "control: attribute alone blocks the write",
        code == 0
        and bool(mask & FILE_ATTRIBUTE_READONLY)
        and (not wrote)
        and "PermissionError" in detail,
        f"attrib rc={code} mask=0x{mask:03X} {detail}",
    )

    code, text = run_command(["attrib", "-R", str(target)])
    wrote, detail = write_attempt(target)
    check(
        "control: write works again once the attribute is cleared",
        code == 0 and wrote,
        f"attrib rc={code} {detail}",
    )

    sid = current_sid()
    if sid is None:
        skip("control: deny ACE checks", "whoami returned no usable SID")
        return
    note(f"current user SID: {sid}")

    code, text = run_command(["icacls", str(target), "/deny", f"*{sid}:(WD,AD)"])
    mask = get_attributes(target) or 0
    wrote, detail = write_attempt(target)
    check(
        "control: attribute cleared + deny ACE blocks the write",
        code == 0
        and not (mask & FILE_ATTRIBUTE_READONLY)
        and (not wrote)
        and "PermissionError" in detail,
        f"icacls rc={code} mask=0x{mask:03X} {detail}",
    )

    child_denied, child_detail = child_write_attempt(target)
    check(
        "control: a fresh interpreter is denied too",
        child_denied,
        child_detail,
    )

    code, text = run_command(["icacls", str(target), "/remove:d", f"*{sid}"])
    wrote, detail = write_attempt(target)
    check(
        "control: removing the deny ACE restores the write",
        code == 0 and wrote,
        f"icacls rc={code} {detail}",
    )


def release_protection(root: Path) -> None:
    """Best-effort: never leave a temp file that blocks its own cleanup."""
    if not IS_WINDOWS:
        return
    sid = current_sid()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        run_command(["attrib", "-R", str(path)])
        if sid:
            run_command(["icacls", str(path), "/remove:d", f"*{sid}"])


def main() -> int:
    started = time.time()
    print("F17b: originals guarded by the OS (read-only attribute + deny ACL)")
    with tempfile.TemporaryDirectory(
        prefix="vx-f17b-", ignore_cleanup_errors=True
    ) as raw:
        tmp = Path(raw)
        check_code_mechanism(tmp)
        if IS_WINDOWS:
            try:
                check_readonly_attribute(tmp)
                check_deny_acl(tmp)
                check_harness_control(tmp)
            finally:
                release_protection(tmp)
        else:
            skip(
                "read-only attribute and deny-write ACL",
                f"os.name={os.name!r}: GetFileAttributes and icacls are Windows-only",
            )
            skip(
                "attribute/ACL harness control",
                f"os.name={os.name!r}: no Windows mechanisms to control",
            )

    elapsed = time.time() - started
    check(
        f"suite within the {CEILING_SECONDS:.0f}s ceiling",
        elapsed <= CEILING_SECONDS,
        f"{elapsed:.1f}s",
    )
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
