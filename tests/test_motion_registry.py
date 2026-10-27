"""Motion Studio template registry -- lazy, linted, resilient.

    python tests/test_motion_registry.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass. No browser, no network, no template is created inside the repo: the
temp templates live in a TemporaryDirectory and the only real template read is
the shipped `vx-title-lower-third`.

What it proves:

a. the shipped template registers, and `frames` is `fps * duration`;
b. three temp templates -- one good, one with a remote `<script src>`, one with
   a malformed manifest -- admit exactly the good one and block the other two,
   each blocked entry carrying its findings;
c. `validate_call` merges defaults and refuses an unknown key and an
   out-of-domain value with `PARAM_UNKNOWN` / `PARAM_RANGE`;
d. importing the module in a fresh subprocess reads no template directory and
   leaves no module-level `Registry`; even constructing one reads nothing, the
   first call is what scans;
e. `refresh()` sees a template added after the first load, and `add(root)`
   registers a second root without rebuilding the process;
f. `describe()` is byte-identical across two calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import motion, motion_registry  # noqa: E402

SCRIPTS = ROOT / "scripts"
TEMPLATE = ROOT / "assets" / "templates" / "vx-title-lower-third"
SHIPPED = "vx-title-lower-third"
SHIPPED_HTML = (TEMPLATE / "index.html").read_text(encoding="utf-8")

PASS = 0
FAIL = 0

LAZY_SNIPPET = r"""
import json
from pathlib import Path

seen = []
original = Path.iterdir


def counting(self):
    seen.append(str(self))
    return original(self)


Path.iterdir = counting

import vx.motion_registry as registry_module
from vx import motion

module_instances = [
    name
    for name, value in vars(registry_module).items()
    if isinstance(value, registry_module.Registry)
]
import_seen = list(seen)
reg = registry_module.Registry()
construct_seen = list(seen)
names = reg.names()
call_seen = list(seen)
root = str(motion.templates_root())


def under(items):
    return [path for path in items if path == root or path.startswith(root + "\\")]


print(
    "RESULT "
    + json.dumps(
        {
            "module_instances": module_instances,
            "import_reads": under(import_seen),
            "construct_reads": under(construct_seen),
            "call_reads": under(call_seen),
            "names": names,
        }
    )
)
"""


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<58} {detail}")


def note(label: str, detail: str) -> None:
    print(f"    [INFO] {label:<58} {detail}")


def manifest_text(**overrides: Any) -> str:
    data: Dict[str, Any] = {
        "name": "probe",
        "fps": 30,
        "duration": 1.0,
        "size": [320, 320],
        "params": {
            "label": {"type": "string", "max_length": 12, "default": "ciao"},
        },
    }
    data.update(overrides)
    return json.dumps(data, indent=2)


def write_template(root: Path, dirname: str, *, html: str, meta: str) -> Path:
    directory = root / dirname
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(html, encoding="utf-8")
    (directory / "template.json").write_text(meta, encoding="utf-8")
    return directory


def codes_of(blocked_entry: Dict[str, Any]) -> List[str]:
    return [str(item["code"]) for item in blocked_entry["findings"]]


def case_a() -> None:
    print("\n a. the shipped library registers")
    reg = motion_registry.Registry()
    names = reg.names()
    check(
        "the shipped template is still admitted",
        SHIPPED in names,
        f"{len(names)} admitted",
    )
    # The exact inventory (54 names, 4 per family, 10 subtitles) is pinned once,
    # in tests/test_motion_library.py. Here the subject is the catalogue's own
    # contract: admission, ordering, uniqueness and an empty blocked().
    check(
        "names() is sorted and unique",
        names == sorted(set(names)),
        f"{len(names)} names",
    )
    check(
        "the catalogue holds the promised library, not one template",
        len(names) >= 40,
        f"{len(names)} templates",
    )
    check("blocked() is empty on a clean tree", reg.blocked() == {}, str(reg.blocked()))
    entry = reg.get(SHIPPED)
    check("get() returns the entry", entry is not None and entry.name == SHIPPED)
    listed = reg.list()
    check(
        "list() returns entries in name order",
        [item.name for item in listed] == names,
        f"{len(listed)} entries",
    )

    described = reg.describe()
    row = described.get(SHIPPED, {})
    check(
        "describe() carries every promised key",
        set(row)
        == {
            "name",
            "title",
            "category",
            "params",
            "fps",
            "duration",
            "size",
            "frames",
        },
        str(sorted(row)),
    )
    check(
        "describe().frames == fps * duration",
        row.get("frames") == row.get("fps", 0) * row.get("duration", 0),
        f"frames={row.get('frames')} fps={row.get('fps')} duration={row.get('duration')}",
    )
    check(
        "title and category are derived from the name",
        row.get("title") == "Title Lower Third" and row.get("category") == "title",
        f"{row.get('title')!r} / {row.get('category')!r}",
    )
    check(
        "params() lists the four declared parameters",
        sorted(reg.params(SHIPPED)) == ["accent", "position", "subtitle", "title"],
        str(sorted(reg.params(SHIPPED))),
    )


def case_b(work: Path) -> None:
    print("\n b. a mixed tree admits the good one and blocks the other two")
    root = work / "mixed"
    write_template(
        root, "good-one", html=SHIPPED_HTML, meta=manifest_text(name="probe-good")
    )
    remote_html = SHIPPED_HTML.replace(
        "</head>",
        '<script src="https://cdn.example.com/remote.js"></script>\n</head>',
        1,
    )
    write_template(
        root, "remote-one", html=remote_html, meta=manifest_text(name="probe-remote")
    )
    write_template(
        root,
        "broken-one",
        html=SHIPPED_HTML,
        meta='{"name": "probe-broken", "fps": 30,',
    )

    reg = motion_registry.Registry(root)
    check(
        "names() lists only the good template",
        reg.names() == ["probe-good"],
        str(reg.names()),
    )
    blocked = reg.blocked()
    check(
        "blocked() names the two refused directories",
        sorted(blocked) == ["broken-one", "remote-one"],
        str(sorted(blocked)),
    )
    remote_codes = codes_of(blocked.get("remote-one", {"findings": []}))
    check(
        "the remote script is blocked as NETWORK_TAG",
        "NETWORK_TAG" in remote_codes,
        str(remote_codes),
    )
    broken_codes = codes_of(blocked.get("broken-one", {"findings": []}))
    check(
        "the malformed manifest is blocked as MANIFEST_INVALID",
        "MANIFEST_INVALID" in broken_codes,
        str(broken_codes),
    )
    check(
        "blocked findings are non-empty and carry a location",
        all(
            entry["findings"] and all(item.get("where") for item in entry["findings"])
            for entry in blocked.values()
        ),
        str({key: codes_of(entry) for key, entry in blocked.items()}),
    )
    check(
        "get()/describe() never expose a blocked template",
        reg.get("remote-one") is None and "remote-one" not in reg.describe(),
    )

    refused = None
    try:
        reg.validate_call("broken-one", {})
    except motion_registry.RegistryError as exc:
        refused = exc
    check(
        "calling a blocked template fails as TEMPLATE_BLOCKED",
        refused is not None and refused.code == "TEMPLATE_BLOCKED",
        getattr(refused, "code", "no error"),
    )

    row = reg.describe().get("probe-good", {})
    check(
        "the good neighbour still describes correctly",
        row.get("frames") == 30 and row.get("size") == [320, 320],
        f"frames={row.get('frames')} size={row.get('size')}",
    )
    note("this registry saw", f"{len(reg.names())} admitted / {len(blocked)} blocked")


def case_c() -> None:
    print("\n c. validate_call merges defaults and refuses bad calls")
    reg = motion_registry.Registry()
    merged = reg.validate_call(SHIPPED, {"title": "Solo titolo"})
    check(
        "defaults fill the parameters that were not given",
        merged
        == {
            "title": "Solo titolo",
            "subtitle": "Ruolo, Citta",
            "accent": "#FFD400",
            "position": "bottom-left",
        },
        str(merged),
    )
    check(
        "an empty call is all defaults",
        reg.validate_call(SHIPPED, None) == reg.validate_call(SHIPPED, {}),
    )

    unknown = None
    try:
        reg.validate_call(SHIPPED, {"nope": 1})
    except motion.MotionError as exc:
        unknown = exc
    check(
        "an unknown key fails as PARAM_UNKNOWN naming it",
        unknown is not None
        and unknown.code == "PARAM_UNKNOWN"
        and unknown.payload.get("param") == "nope",
        getattr(unknown, "code", "no error"),
    )

    out_of_domain = None
    try:
        reg.validate_call(SHIPPED, {"accent": "#123456"})
    except motion.MotionError as exc:
        out_of_domain = exc
    check(
        "an out-of-domain value fails as PARAM_RANGE naming it",
        out_of_domain is not None
        and out_of_domain.code == "PARAM_RANGE"
        and out_of_domain.payload.get("param") == "accent",
        getattr(out_of_domain, "code", "no error"),
    )
    domain = out_of_domain.payload.get("domain") if out_of_domain is not None else None
    check(
        "PARAM_RANGE carries the domain",
        isinstance(domain, list) and "#FFD400" in domain,
        str(domain),
    )

    position = None
    try:
        reg.validate_call(SHIPPED, {"position": "middle"})
    except motion.MotionError as exc:
        position = exc
    check(
        "an out-of-domain enum is also PARAM_RANGE",
        position is not None and position.code == "PARAM_RANGE",
        getattr(position, "code", "no error"),
    )


def case_d() -> None:
    print("\n d. import is lazy (fresh subprocess)")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", LAZY_SNIPPET],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    payload: Dict[str, Any] = {}
    for line in completed.stdout.splitlines():
        if line.startswith("RESULT "):
            payload = json.loads(line[len("RESULT ") :])
    check(
        "the subprocess imported the module",
        completed.returncode == 0 and bool(payload),
        completed.stderr.strip().splitlines()[-1]
        if completed.stderr.strip()
        else "no stderr",
    )
    check(
        "no module-level Registry instance exists",
        payload.get("module_instances") == [],
        str(payload.get("module_instances")),
    )
    check(
        "import read no template directory",
        payload.get("import_reads") == [],
        str(payload.get("import_reads")),
    )
    check(
        "constructing a Registry reads no template directory",
        payload.get("construct_reads") == [],
        str(payload.get("construct_reads")),
    )
    names = payload.get("names") or []
    check(
        "the first call performs the scan",
        payload.get("call_reads") != [] and SHIPPED in names and len(names) >= 40,
        f"reads={payload.get('call_reads')} names={len(names)}",
    )


def case_e(work: Path) -> None:
    print("\n e. refresh() and add() pick up templates after the first load")
    root_a = work / "growing"
    write_template(
        root_a, "one", html=SHIPPED_HTML, meta=manifest_text(name="probe-one")
    )
    reg = motion_registry.Registry(root_a)
    check(
        "the first load sees one template",
        reg.names() == ["probe-one"],
        str(reg.names()),
    )

    write_template(
        root_a, "two", html=SHIPPED_HTML, meta=manifest_text(name="probe-two")
    )
    returned = reg.refresh()
    check(
        "refresh() rescans in place and returns self",
        returned is reg and reg.names() == ["probe-one", "probe-two"],
        str(reg.names()),
    )

    root_b = work / "second-root"
    write_template(
        root_b, "three", html=SHIPPED_HTML, meta=manifest_text(name="probe-three")
    )
    returned = reg.add(root_b)
    check(
        "add(root) registers a second root and returns self",
        returned is reg
        and reg.names() == ["probe-one", "probe-three", "probe-two"]
        and root_b in reg.roots(),
        str(reg.names()),
    )
    check(
        "the second root's template describes correctly",
        reg.describe().get("probe-three", {}).get("frames") == 30,
        str(reg.describe().get("probe-three", {}).get("frames")),
    )


def case_f() -> None:
    print("\n f. describe() is deterministic")
    reg = motion_registry.Registry()
    first = json.dumps(reg.describe(), sort_keys=True, ensure_ascii=False)
    second = json.dumps(reg.describe(), sort_keys=True, ensure_ascii=False)
    check("two calls produce the same string", first == second, f"{len(first)} chars")


def run(work: Path) -> None:
    case_a()
    case_b(work)
    case_c()
    case_d()
    case_e(work)
    case_f()


def main() -> int:
    global PASS, FAIL
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_registry_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        FAIL += 1
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
