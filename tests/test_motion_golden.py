"""F3 -- the golden gate for the Motion Studio template library.

    python tests/test_motion_golden.py

Script-style: every check prints PASS/FAIL, the process exits 0 only when all
checks pass. It never writes a fixture -- ``tests/motion_golden.py`` is the only
writer -- and it launches a browser once, only to read the version the fixtures
must have been blessed against.

What it proves:

a. every template the registry admits has a manifest, and every manifest belongs
   to an admitted template;
b. each manifest is internally consistent: template name, ``engine`` family,
   ``engine_version`` against the current browser, fps, frame count, size, and
   the three instants at indices 0, count//2 and count-1;
c. each instant matches its recorded sha256 and byte size, and its ``t`` is the
   virtual time of its index;
d. ``index.json`` agrees with the per-template manifests (schema, template and
   frame counts, engine, version, ISO date only);
e. no fixture value is an absolute path, neither a drive-letter path nor one
   starting with '/';
f. the guard itself works: an intact copy of a fixture PNG compares clean and a
   single flipped byte is detected, in a temp dir, never on the repo copy;
g. the library is incomplete without crashing: a template with no fixture is a
   FAIL with its name, never a skip.

The whole file skips only when ``motion.available()`` reports no system browser,
which is the same optional-dependency rule the other suites use. A missing
manifest, a stale fixture or a corrupted pixel is a failure, never a skip.

Pixels and metadata are judged separately. A pixel mismatch -- a different
sha256, a different byte size, a frame that moved -- is a FAIL. A manifest whose
bytes still match but whose ``engine_version`` differs from the running browser
is a WARN: the guard judges images, not build numbers, so the suite stays green
and both versions are printed. Re-bless with::

    python tests/motion_golden.py --write
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from vx import motion  # noqa: E402
from vx.motion_registry import Registry  # noqa: E402

import motion_golden  # noqa: E402

FIXTURES = motion_golden.FIXTURES
SCHEMA = motion_golden.SCHEMA
EXPECTED_ENGINE = motion_golden.ENGINE
EXPECTED_FPS = motion_golden.FPS
INSTANT_COUNT = motion_golden.INSTANT_COUNT
CEILING_SECONDS = 900.0

#: Built from parts so the checker never contains the literal path it rejects.
_DRIVE = re.compile("^[A-Za-z]" + ":" + r"[\\/]")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PASS = 0
FAIL = 0
SKIP = 0
WARN = 0
IMAGES = 0
DRIFTS: List[Tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += int(bool(ok))
    FAIL += int(not ok)
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:<58} {detail}")


def warn(label: str, detail: str = "") -> None:
    """A tolerated drift: the pixels agree, only the build number moved.

    It counts as a pass -- the suite stays green -- and in the warn tally so the
    verdict can say how much of the green is metadata drift rather than a clean
    match.
    """
    global PASS, WARN
    PASS += 1
    WARN += 1
    print(f"    [WARN] {label:<58} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:<58} {reason}")


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _walk_strings(payload: Any, prefix: str = "") -> Iterator[Tuple[str, str]]:
    if isinstance(payload, str):
        yield prefix or "<root>", payload
    elif isinstance(payload, dict):
        for key in payload:
            yield from _walk_strings(
                payload[key], f"{prefix}.{key}" if prefix else str(key)
            )
    elif isinstance(payload, list):
        for position, item in enumerate(payload):
            yield from _walk_strings(item, f"{prefix}[{position}]")


def _absolute_hit(value: str) -> bool:
    return bool(_DRIVE.match(value)) or value.startswith("/")


def version_drift(recorded: Any, current: Optional[str]) -> Optional[Tuple[str, str]]:
    """The metadata half of the comparison, isolated and pure.

    Returns ``(blessed, running)`` when the manifest was sealed on a different
    browser build, ``None`` when the versions agree or the browser could not be
    read. It never looks at a pixel: that is ``compare_instant``'s job.
    """
    if current is None:
        return None
    blessed = str(recorded or "")
    if blessed == current:
        return None
    return blessed or "(empty)", current


def judge(
    pixel_problems: Sequence[str],
    drift: Optional[Tuple[str, str]],
) -> Tuple[str, str]:
    """Decide a check from two independent facts: pixels first, version second.

    * any pixel problem -> ``("fail", detail)``, even when the versions match;
    * matching pixels and a version drift -> ``("warn", detail)``;
    * matching pixels and matching versions -> ``("pass", "")``.
    """
    if pixel_problems:
        return "fail", "; ".join(list(pixel_problems)[:2])
    if drift is not None:
        return "warn", f"engine {drift[0]} -> {drift[1]}"
    return "pass", ""


def report(
    label: str,
    pixel_problems: Sequence[str],
    drift: Optional[Tuple[str, str]],
    pass_detail: str = "",
) -> None:
    """Print one check, routing pixel failures to FAIL and drift only to WARN."""
    kind, detail = judge(pixel_problems, drift)
    if kind == "fail":
        check(label, False, detail)
        return
    if drift is not None and drift not in DRIFTS:
        DRIFTS.append(drift)
    if kind == "warn":
        warn(label, detail)
    else:
        check(label, True, pass_detail)


def compare_instant(instant: Mapping[str, Any], directory: Path) -> List[str]:
    """The sha256/byte comparison, isolated so the guard can be proven on a copy."""
    file_name = instant.get("file")
    if not isinstance(file_name, str) or not file_name:
        return ["has no file name"]
    target = Path(directory) / file_name
    if not target.is_file():
        return [f"{file_name} is missing"]
    data = target.read_bytes()
    problems: List[str] = []
    recorded_bytes = instant.get("bytes")
    if (
        not isinstance(recorded_bytes, int)
        or isinstance(recorded_bytes, bool)
        or len(data) != recorded_bytes
    ):
        problems.append(f"bytes {len(data)} != manifest {recorded_bytes!r}")
    digest = hashlib.sha256(data).hexdigest()
    recorded_sha = instant.get("sha256")
    if digest != recorded_sha:
        problems.append(
            f"sha256 {digest[:12]}... != manifest {str(recorded_sha)[:12]}..."
        )
    return problems


def verify_manifest(
    name: str,
    manifest: Mapping[str, Any],
    directory: Path,
) -> List[str]:
    """Everything the manifest must get right to prove the pixels.

    ``engine_version`` is deliberately absent: it is metadata drift, judged by
    ``version_drift``/``judge`` and reported as a WARN, never as a pixel failure.
    """
    problems: List[str] = []
    if manifest.get("template") != name:
        problems.append(f"template is {manifest.get('template')!r}, expected {name!r}")
    if manifest.get("engine") != EXPECTED_ENGINE:
        problems.append(
            f"engine is {manifest.get('engine')!r}, expected {EXPECTED_ENGINE!r}"
        )
    fps = manifest.get("fps")
    numeric_fps = isinstance(fps, (int, float)) and not isinstance(fps, bool)
    if not numeric_fps or float(fps) != EXPECTED_FPS:
        problems.append(f"fps is {fps!r}, expected {EXPECTED_FPS}")
    size = manifest.get("size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in size
        )
    ):
        problems.append(f"size is {size!r}, expected [width, height] positive integers")
    count = manifest.get("frame_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < INSTANT_COUNT:
        problems.append(f"frame_count is {count!r}, expected >= {INSTANT_COUNT}")
        return problems
    instants = manifest.get("instants")
    if not isinstance(instants, list) or len(instants) != INSTANT_COUNT:
        listed = len(instants) if isinstance(instants, list) else "no"
        problems.append(f"instants has {listed} entries, expected {INSTANT_COUNT}")
        return problems
    expected_indices = [0, count // 2, count - 1]
    found_indices = [
        item.get("index") if isinstance(item, dict) else None for item in instants
    ]
    if found_indices != expected_indices:
        problems.append(f"instant indices {found_indices} != {expected_indices}")
    rate = float(fps) if numeric_fps else EXPECTED_FPS
    for slot, item in enumerate(instants):
        if not isinstance(item, dict):
            problems.append(f"instant {slot}: not an object")
            continue
        file_name = item.get("file")
        if (
            not isinstance(file_name, str)
            or not file_name
            or "/" in file_name
            or "\\" in file_name
        ):
            problems.append(
                f"instant {slot}: file {file_name!r} is not a relative POSIX name"
            )
            continue
        expected_file = f"instant-{slot:02d}.png"
        if file_name != expected_file:
            problems.append(f"instant {slot}: file {file_name!r} != {expected_file!r}")
        index = item.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            expected_t = round(index / rate, 6)
            moment = item.get("t")
            numeric_t = isinstance(moment, (int, float)) and not isinstance(
                moment, bool
            )
            if not numeric_t or abs(float(moment) - expected_t) > 1e-6:
                problems.append(
                    f"instant {slot}: t {moment!r} != {expected_t} for index {index}"
                )
        problems.extend(
            f"instant {slot} ({file_name}): {problem}"
            for problem in compare_instant(item, directory)
        )
    return problems


def verify_index(
    index: Mapping[str, Any],
    manifests: Mapping[str, Mapping[str, Any]],
) -> List[str]:
    """The structural half of index.json; its ``engine_version`` is drift, not fail."""
    problems: List[str] = []
    if index.get("schema") != SCHEMA:
        problems.append(f"schema is {index.get('schema')!r}, expected {SCHEMA!r}")
    if index.get("engine") != EXPECTED_ENGINE:
        problems.append(
            f"engine is {index.get('engine')!r}, expected {EXPECTED_ENGINE!r}"
        )
    if index.get("templates") != len(manifests):
        problems.append(
            f"templates is {index.get('templates')!r}, "
            f"the manifests on disk are {len(manifests)}"
        )
    expected_frames = sum(
        len(manifest.get("instants") or []) for manifest in manifests.values()
    )
    if index.get("frames") != expected_frames:
        problems.append(
            f"frames is {index.get('frames')!r}, the manifests hold {expected_frames}"
        )
    generated = index.get("generated_at")
    if not isinstance(generated, str) or not _ISO_DATE.fullmatch(generated):
        problems.append(
            f"generated_at is {generated!r}, expected an ISO date (YYYY-MM-DD)"
        )
    return problems


def browser_version() -> str:
    """Read the version of the browser the fixtures must have been blessed on."""
    probe = motion.available()
    if not probe["available"]:
        return ""
    pw = browser = None
    try:
        pw, browser, _engine = motion._launch(probe["candidates"])
        return str(getattr(browser, "version", "") or "")
    except Exception:  # noqa: BLE001 - an unreadable version is reported, not fatal
        return ""
    finally:
        motion._close_quietly(None, None, browser, pw)


def _tiny_png() -> bytes:
    """A valid 1x1 transparent PNG, for the guard proof when no fixture exists."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def prove_guard(work: Path) -> None:
    """Copy one fixture into a temp dir, corrupt it, and show the check sees it."""
    candidates = sorted(FIXTURES.glob("*/instant-*.png")) if FIXTURES.is_dir() else []
    if candidates:
        source = candidates[0]
        data = source.read_bytes()
        origin = _relative(source)
    else:
        data = _tiny_png()
        origin = "a synthetic 1x1 PNG (no fixture exists yet)"
    target_dir = work / "guard"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "instant-00.png"
    target.write_bytes(data)
    instant = {
        "index": 0,
        "t": 0.0,
        "file": target.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    intact = compare_instant(instant, target_dir)
    damaged = bytearray(data)
    damaged[len(damaged) // 2] ^= 0xFF
    target.write_bytes(bytes(damaged))
    corrupted = compare_instant(instant, target_dir)
    check("guard: an intact copy compares clean", not intact, f"source={origin}")
    check(
        "guard: one flipped byte is detected",
        bool(corrupted),
        "; ".join(corrupted) or "not detected",
    )


def prove_judgement(work: Path) -> None:
    """Prove the pixel/metadata split with no browser and no fixture.

    One manifest, blessed on an older build, is compared twice: once against the
    bytes it records (identical frames) and once against a single flipped byte.
    The version mismatch is the same in both cases, so the only thing that can
    change the verdict is the pixel: identical -> WARN, changed -> FAIL.
    """
    directory = work / "judgement"
    directory.mkdir(parents=True, exist_ok=True)
    data = _tiny_png()
    target = directory / "instant-00.png"
    target.write_bytes(data)
    manifest: Dict[str, Any] = {
        "engine_version": "156.0.8078.74",
        "instants": [
            {
                "index": 0,
                "t": 0.0,
                "file": target.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        ],
    }
    running = "156.0.8078.80"
    drift = version_drift(manifest["engine_version"], running)
    identical = compare_instant(manifest["instants"][0], directory)
    kind, detail = judge(identical, drift)
    check(
        "judgement: identical frames + version drift is a warn",
        kind == "warn" and not identical,
        f"{kind}: {detail}",
    )
    kind, detail = judge(
        identical, version_drift(manifest["engine_version"], "156.0.8078.74")
    )
    check(
        "judgement: identical frames + same version is a pass",
        kind == "pass" and not identical,
        f"{kind}: {'no drift' if kind == 'pass' else detail}",
    )
    damaged = bytearray(data)
    damaged[len(damaged) // 2] ^= 0xFF
    target.write_bytes(bytes(damaged))
    moved = compare_instant(manifest["instants"][0], directory)
    kind, detail = judge(moved, drift)
    check(
        "judgement: different frames stay a fail despite the drift",
        kind == "fail" and bool(moved),
        f"{kind}: {detail}",
    )


def run(work: Path) -> None:
    registry = Registry()
    names = registry.names()
    check("registry admits templates", bool(names), f"{len(names)} template(s)")
    check(
        "registry includes the shipped title template",
        "vx-title-lower-third" in names,
        f"{len(names)} template(s)",
    )

    manifest_dirs = (
        sorted(
            child.name
            for child in FIXTURES.iterdir()
            if child.is_dir() and (child / "manifest.json").is_file()
        )
        if FIXTURES.is_dir()
        else []
    )
    missing = [name for name in names if name not in manifest_dirs]
    missing_detail = ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")
    check(
        "every admitted template has a golden manifest",
        not missing,
        f"{len(missing)} missing of {len(names)}: {missing_detail}"
        if missing
        else f"{len(names)} template(s) covered",
    )
    stale = [name for name in manifest_dirs if name not in names]
    check(
        "every golden manifest belongs to an admitted template",
        not stale,
        f"stale: {', '.join(stale[:6])}" if stale else "none",
    )

    current_version = browser_version()
    check(
        "the current browser reports a version",
        bool(current_version),
        current_version or "unreadable",
    )
    comparable_version: Optional[str] = current_version or None

    index_path = FIXTURES / "index.json"
    index = _load_json(index_path)
    check(
        "index.json exists and parses", isinstance(index, dict), _relative(index_path)
    )

    manifests: Dict[str, Dict[str, Any]] = {}
    for name in manifest_dirs:
        manifest_path = FIXTURES / name / "manifest.json"
        payload = _load_json(manifest_path)
        if not isinstance(payload, dict):
            check(f"{name}: manifest parses", False, _relative(manifest_path))
            continue
        manifests[name] = payload
        problems = verify_manifest(name, payload, FIXTURES / name)
        report(
            f"{name}: manifest matches its instants",
            problems,
            version_drift(payload.get("engine_version"), comparable_version),
            "3 instants, sha256 and bytes agree",
        )

    if isinstance(index, dict):
        problems = verify_index(index, manifests)
        report(
            "index.json matches the per-template manifests",
            problems,
            version_drift(index.get("engine_version"), comparable_version),
            "counts, engine and date agree",
        )

    payloads: List[Tuple[str, Any]] = []
    if isinstance(index, dict):
        payloads.append(("index.json", index))
    payloads.extend(
        (f"{name}/manifest.json", manifest) for name, manifest in manifests.items()
    )
    offenders: List[str] = []
    for label, payload in payloads:
        for where, value in _walk_strings(payload):
            if _absolute_hit(value):
                offenders.append(f"{label}:{where}={value!r}")
    check(
        "no fixture carries an absolute path",
        not offenders,
        "; ".join(offenders[:3]) if offenders else f"{len(payloads)} file(s) scanned",
    )

    re_render(work, manifests)

    prove_guard(work)


def re_render(work: Path, manifests: Mapping[str, Mapping[str, Any]]) -> None:
    """Capture every template again and compare the pixels with its manifest.

    Comparing the fixtures with the manifest that produced them proves nothing
    about the engine: it only proves the files have not been edited. The seal is
    real only if the browser is asked for the same instants today and answers
    with the same bytes. `workdir=None` keeps the frame cache out of it.

    This check is pixels only: a different sha256 is a FAIL. Version drift is
    already declared once, by the manifest check, so it is not repeated here --
    that keeps one WARN per drifted manifest instead of two.
    """
    global IMAGES
    registry = Registry()
    for name, manifest in sorted(manifests.items()):
        entry = registry.get(name)
        if entry is None:
            check(
                f"{name}: live re-render",
                False,
                "the manifest names a template the registry no longer admits",
            )
            continue
        size = manifest.get("size") or [0, 0]
        try:
            result = motion.capture(
                entry.directory,
                {},
                work / "live" / name,
                fps=float(manifest.get("fps") or 3.0),
                size=(int(size[0]), int(size[1])),
                workdir=None,
            )
        except motion.MotionError as exc:
            check(f"{name}: live re-render", False, f"{exc.code}: {exc}")
            continue
        if result.get("engine") not in ("chrome", "msedge"):
            check(
                f"{name}: live re-render runs on a browser",
                False,
                f"engine={result.get('engine')!r} is not a Chromium frame",
            )
            continue
        live = [Path(item) for item in result["frames"]]
        problems: List[str] = []
        for instant in manifest.get("instants") or ():
            index = int(instant["index"])
            if index >= len(live):
                problems.append(f"instant {index} beyond {len(live)} live frames")
                continue
            IMAGES += 1
            digest = hashlib.sha256(live[index].read_bytes()).hexdigest()
            if digest != instant["sha256"]:
                problems.append(
                    f"instant {index} at t={instant['t']}: "
                    f"{digest[:12]} != {instant['sha256'][:12]}"
                )
        check(
            f"{name}: live re-render is byte-identical",
            not problems,
            "; ".join(problems[:2]) if problems else f"{len(live)} frames re-rendered",
        )


def _drift_note() -> str:
    """The single engine-version pair the verdict must declare, if any."""
    if not DRIFTS:
        return ""
    pairs = sorted(set(DRIFTS))
    return " (engine " + ", ".join(f"{old} -> {new}" for old, new in pairs) + ")"


def main() -> int:
    global PASS, FAIL, SKIP, WARN
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="vx_motion_judge_", ignore_cleanup_errors=True
    ) as judgement_tmp:
        prove_judgement(Path(judgement_tmp))
    probe = motion.available()
    if not probe["available"]:
        skip("motion golden gate", str(probe.get("reason") or "no system browser"))
        print(f"\n{PASS} PASS / {FAIL} FAIL / {WARN} warn / {SKIP} SKIP")
        return 0
    try:
        with tempfile.TemporaryDirectory(
            prefix="vx_motion_golden_gate_", ignore_cleanup_errors=True
        ) as tmp:
            run(Path(tmp))
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, never a skip
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        FAIL += 1
    elapsed = time.perf_counter() - started
    slow = elapsed > CEILING_SECONDS
    print(
        f"\n{PASS} PASS / {FAIL} FAIL / {WARN} warn / {SKIP} SKIP, "
        f"{IMAGES} images compared{_drift_note()}, {elapsed:.1f}s "
        f"(ceiling {CEILING_SECONDS:.0f}s)"
    )
    if slow:
        print(f"FAIL: suite took {elapsed:.1f}s, above the ceiling")
        return 1
    return 0 if FAIL == 0 and not slow else 1


if __name__ == "__main__":
    raise SystemExit(main())
