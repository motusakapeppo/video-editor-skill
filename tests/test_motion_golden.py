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

Pixels and metadata are judged separately. On a text-free template a pixel
mismatch -- a different sha256, a different byte size, a frame that moved -- is
still a FAIL. A template that renders copy may miss by antialias only when both
limits hold: max channel delta <= 8/255 and <= 0.02% of the frame changed.
Anything larger is a FAIL with the measured delta and pixel count, and every
tolerated image is named with those numbers in the verdict. A manifest whose
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
from dataclasses import dataclass
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

#: Only a template that renders copy may drift by a hairline of antialias.
#: The two limits are ANDed: one channel may move by at most 8/255 and at most
#: 0.02% of the frame may change (414 px at 1080x1920). A template with no text
#: gets neither limit and stays byte-identical.
MAX_TEXT_CHANNEL_DELTA = 8
MAX_TEXT_CHANGED_FRACTION = 0.0002

#: Built from parts so the checker never contains the literal path it rejects.
_DRIVE = re.compile("^[A-Za-z]" + ":" + r"[\\/]")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TEXT_MARKUP = re.compile(r"\.(?:textContent|innerText|innerHTML)\s*=")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

PASS = 0
FAIL = 0
SKIP = 0
WARN = 0
IMAGES = 0
DRIFTS: List[Tuple[str, str]] = []


@dataclass(frozen=True)
class FrameDiff:
    """What a pixel comparison measured, before any limit is applied."""

    max_delta: int
    changed: int
    total: int

    @property
    def percent(self) -> float:
        return (self.changed * 100.0 / self.total) if self.total else 0.0


@dataclass(frozen=True)
class ToleranceNote:
    """One image that only passed because the text-template limit applied."""

    template: str
    index: int
    max_delta: int
    changed: int
    total: int
    engine_version: str


TOLERANCE_NOTES: List[ToleranceNote] = []


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


def template_has_text(directory: Path) -> Tuple[bool, str]:
    """Does this template render copy, and therefore earn the antialias limit?

    The declaration wins: a ``string`` parameter that is not an enumerated word
    (``font``, ``palette``, ``align`` all carry ``values``) and has a default
    containing a letter is copy the template will typeset. Backgrounds and
    transitions declare only ``font``/``palette``/colours/numbers and are
    text-free. The HTML is the fallback, for a template that fills a node
    without declaring a text parameter.
    """
    manifest = _load_json(Path(directory) / "template.json")
    if isinstance(manifest, dict):
        params = manifest.get("params")
        if isinstance(params, dict):
            for key, spec in params.items():
                if not isinstance(spec, dict) or spec.get("type") != "string":
                    continue
                if "values" in spec:
                    continue
                default = spec.get("default")
                if isinstance(default, str) and _LETTER.search(default):
                    return True, f"template.json declares text param {key!r}"
    try:
        markup = (Path(directory) / "index.html").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        markup = ""
    if markup and _TEXT_MARKUP.search(markup):
        return True, "index.html fills a text node"
    return False, "no text parameter and no rendered text"


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    dist_left = abs(estimate - left)
    dist_above = abs(estimate - above)
    dist_upper = abs(estimate - upper_left)
    if dist_left <= dist_above and dist_left <= dist_upper:
        return left
    if dist_above <= dist_upper:
        return above
    return upper_left


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """Undo the five PNG scanline filters. 8-bit, non-interlaced only."""
    stride = width * channels
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ValueError(f"PNG holds {len(raw)} bytes, needs {expected}")
    bpp = channels
    output = bytearray(stride * height)
    previous = bytearray(stride)
    cursor = 0
    for row in range(height):
        kind = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        if kind == 0:
            pass
        elif kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upper = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper)) & 0xFF
        else:
            raise ValueError(f"unknown PNG filter {kind} on row {row}")
        output[row * stride : (row + 1) * stride] = line
        previous = line
    return output


def _to_rgba(pixels: bytearray, width: int, height: int, colour: int) -> bytearray:
    """Normalise an 8-bit PNG sample stream to RGBA, so comparisons line up."""
    count = width * height
    if colour == 6:
        return pixels
    rgba = bytearray(count * 4)
    if colour == 2:
        rgba[0::4] = pixels[0::3]
        rgba[1::4] = pixels[1::3]
        rgba[2::4] = pixels[2::3]
        rgba[3::4] = b"\xff" * count
    elif colour == 0:
        rgba[0::4] = pixels
        rgba[1::4] = pixels
        rgba[2::4] = pixels
        rgba[3::4] = b"\xff" * count
    elif colour == 4:
        rgba[0::4] = pixels[0::2]
        rgba[1::4] = pixels[0::2]
        rgba[2::4] = pixels[0::2]
        rgba[3::4] = pixels[1::2]
    else:
        raise ValueError(f"unsupported PNG colour type {colour}")
    return rgba


def read_png(path: Path) -> Tuple[int, int, bytearray]:
    """Decode an 8-bit PNG to (width, height, RGBA bytes), stdlib only."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    cursor = 8
    header: Optional[Tuple[int, int, int, int, int, int, int]] = None
    compressed = bytearray()
    while cursor + 8 <= len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        tag = data[cursor + 4 : cursor + 8]
        body = data[cursor + 8 : cursor + 8 + length]
        cursor += 12 + length
        if tag == b"IHDR":
            if len(body) != 13:
                raise ValueError("malformed IHDR")
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            compressed.extend(body)
        elif tag == b"IEND":
            break
    if header is None:
        raise ValueError("PNG has no IHDR")
    width, height, depth, colour, compression, filtering, interlace = header
    if depth != 8 or compression != 0 or filtering != 0 or interlace != 0:
        raise ValueError(
            f"unsupported PNG (depth={depth}, compression={compression}, "
            f"filter={filtering}, interlace={interlace})"
        )
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    if channels is None:
        raise ValueError(f"unsupported PNG colour type {colour}")
    pixels = _unfilter(
        zlib.decompress(bytes(compressed)), width, height, channels
    )
    return width, height, _to_rgba(pixels, width, height, colour)


def pixel_difference(golden: Path, live: Path) -> FrameDiff:
    """The measured distance between two frames; no verdict, no limits."""
    width, height, left = read_png(golden)
    other_width, other_height, right = read_png(live)
    if (width, height) != (other_width, other_height):
        raise ValueError(
            f"size {other_width}x{other_height} != golden {width}x{height}"
        )
    total = width * height
    max_delta = 0
    changed = 0
    for i in range(0, total * 4, 4):
        delta = max(
            abs(left[i] - right[i]),
            abs(left[i + 1] - right[i + 1]),
            abs(left[i + 2] - right[i + 2]),
            abs(left[i + 3] - right[i + 3]),
        )
        if delta:
            changed += 1
            if delta > max_delta:
                max_delta = delta
    return FrameDiff(max_delta=max_delta, changed=changed, total=total)


def changed_limit(total: int) -> int:
    """The largest number of changed pixels a text frame may show."""
    return int(total * MAX_TEXT_CHANGED_FRACTION)


def frame_detail(diff: Optional[FrameDiff]) -> str:
    if diff is None:
        return "no pixel measurement"
    return (
        f"delta={diff.max_delta}/255, {diff.changed}/{diff.total} px "
        f"({diff.percent:.5f}%)"
    )


def judge_frame(
    golden: Path,
    live: Path,
    *,
    text_template: bool,
) -> Tuple[List[str], Optional[FrameDiff], bool]:
    """Judge one mismatched frame; return (problems, measurement, tolerated).

    A text template passes only when both limits hold. A text-free template
    never reaches the tolerance: the caller already knows the bytes differ, so
    the frame is a failure even if the decoded pixels happened to agree.
    """
    try:
        diff = pixel_difference(golden, live)
    except (OSError, ValueError, zlib.error) as exc:
        return [f"pixel comparison failed: {type(exc).__name__}: {exc}"], None, False
    limit = changed_limit(diff.total)
    if not text_template:
        return (
            [
                f"{frame_detail(diff)} -- text-free template, byte identity "
                "is required"
            ],
            diff,
            False,
        )
    if diff.max_delta <= MAX_TEXT_CHANNEL_DELTA and diff.changed <= limit:
        return [], diff, True
    return (
        [
            f"{frame_detail(diff)} exceeds max delta "
            f"{MAX_TEXT_CHANNEL_DELTA}/255 or max {limit} changed px "
            f"({MAX_TEXT_CHANGED_FRACTION * 100:g}%)"
        ],
        diff,
        False,
    )


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


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    """Encode 8-bit RGBA as a filter-0 PNG, for the synthetic tolerance proofs."""
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)
        raw.extend(rgba[row * stride : (row + 1) * stride])
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _blank_rgba(width: int, height: int) -> bytearray:
    return bytearray(bytes((18, 18, 22, 255)) * (width * height))


def _set_pixel(
    rgba: bytearray, width: int, x: int, y: int, value: Tuple[int, int, int, int]
) -> None:
    at = (y * width + x) * 4
    rgba[at : at + 4] = bytes(value)


def _fill_block(
    rgba: bytearray,
    width: int,
    x0: int,
    y0: int,
    block_width: int,
    block_height: int,
    value: Tuple[int, int, int, int],
) -> None:
    for y in range(y0, y0 + block_height):
        for x in range(x0, x0 + block_width):
            _set_pixel(rgba, width, x, y, value)


def _write_frame(directory: Path, name: str, width: int, height: int, rgba: bytes) -> Path:
    target = directory / name
    target.write_bytes(_png_bytes(width, height, rgba))
    return target


def prove_tolerance(work: Path) -> None:
    """Prove the antialias tolerance is a ruler, not a blanket amnesty.

    Four synthetic images, no browser: a one-pixel hairline passes only on a
    text template; a solid block, a moved glyph and the same hairline on a
    text-free template all fail, each with its measured values.
    """
    directory = work / "tolerance"
    directory.mkdir(parents=True, exist_ok=True)
    width = height = 200

    base = _blank_rgba(width, height)
    golden = _write_frame(directory, "base.png", width, height, base)

    hairline = bytearray(base)
    _set_pixel(hairline, width, 10, 10, (20, 18, 22, 255))
    hairline_path = _write_frame(directory, "hairline.png", width, height, hairline)
    problems, diff, tolerated = judge_frame(
        golden, hairline_path, text_template=True
    )
    check(
        "tolerance: 1 px delta 2 on a text template passes",
        not problems and tolerated and diff is not None
        and diff.max_delta == 2
        and diff.changed == 1,
        frame_detail(diff) + " (tolerated)",
    )

    block = bytearray(base)
    _fill_block(block, width, 20, 20, 10, 10, (146, 18, 22, 255))
    block_path = _write_frame(directory, "block.png", width, height, block)
    problems, diff, tolerated = judge_frame(golden, block_path, text_template=True)
    check(
        "tolerance: 10x10 block delta 128 on a text template fails",
        bool(problems) and not tolerated and diff is not None
        and diff.max_delta == 128
        and diff.changed == 100,
        frame_detail(diff),
    )

    glyph = bytearray(base)
    _fill_block(glyph, width, 20, 60, 12, 24, (255, 255, 255, 255))
    glyph_path = _write_frame(directory, "glyph-a.png", width, height, glyph)
    moved = bytearray(base)
    _fill_block(moved, width, 40, 60, 12, 24, (255, 255, 255, 255))
    moved_path = _write_frame(directory, "glyph-b.png", width, height, moved)
    problems, diff, tolerated = judge_frame(
        glyph_path, moved_path, text_template=True
    )
    check(
        "tolerance: a moved glyph fails",
        bool(problems) and not tolerated and diff is not None
        and diff.max_delta > MAX_TEXT_CHANNEL_DELTA
        and diff.changed > changed_limit(diff.total),
        frame_detail(diff),
    )

    text_free, _why = template_has_text(
        ROOT / "assets" / "templates" / "vx-background-bokeh"
    )
    problems, diff, tolerated = judge_frame(
        golden, hairline_path, text_template=text_free
    )
    check(
        "tolerance: 1 px delta 2 on a text-free template fails",
        bool(problems) and not tolerated,
        frame_detail(diff),
    )
    text_template, _why = template_has_text(
        ROOT / "assets" / "templates" / "vx-title-kicker"
    )
    check(
        "tolerance: the title opts in and the background stays text-free",
        text_template and not text_free,
        f"vx-title-kicker={'text' if text_template else 'text-free'}, "
        f"vx-background-bokeh={'text' if text_free else 'text-free'}",
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

    This check is pixels only: a different sha256 on a text-free template is a
    FAIL. A text template may miss by the antialias limits only: delta <= 8/255
    and <= 0.02% of the frame. Every tolerated image is recorded with its
    measurement so the verdict can name it. Version drift is already declared
    once, by the manifest check, so it is not repeated here -- that keeps one
    WARN per drifted manifest instead of two.
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
        text_template, text_reason = template_has_text(Path(entry.directory))
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
        engine_version = str(
            result.get("engine_version") or manifest.get("engine_version") or ""
        )
        problems: List[str] = []
        tolerated_here = 0
        for instant in manifest.get("instants") or ():
            index = int(instant["index"])
            if index >= len(live):
                problems.append(f"instant {index} beyond {len(live)} live frames")
                continue
            IMAGES += 1
            digest = hashlib.sha256(live[index].read_bytes()).hexdigest()
            if digest == instant["sha256"]:
                continue
            if not text_template:
                problems.append(
                    f"instant {index} at t={instant['t']}: "
                    f"{digest[:12]} != {instant['sha256'][:12]} "
                    "(text-free, byte identity required)"
                )
                continue
            golden = FIXTURES / name / instant["file"]
            pixel_problems, diff, tolerated = judge_frame(
                golden, live[index], text_template=True
            )
            if tolerated and diff is not None:
                tolerated_here += 1
                TOLERANCE_NOTES.append(
                    ToleranceNote(
                        template=name,
                        index=index,
                        max_delta=diff.max_delta,
                        changed=diff.changed,
                        total=diff.total,
                        engine_version=engine_version,
                    )
                )
                continue
            problems.extend(
                f"instant {index} at t={instant['t']}: {problem}"
                for problem in (pixel_problems or ["pixel comparison refused"])
            )
        if problems:
            detail = "; ".join(problems[:2])
        elif tolerated_here:
            detail = (
                f"{len(live)} frames re-rendered, {tolerated_here} within "
                f"antialias tolerance ({text_reason})"
            )
        else:
            detail = f"{len(live)} frames re-rendered, byte-identical"
        check(f"{name}: live re-render matches its golden", not problems, detail)


def _tolerance_note() -> str:
    """Name every image that passed only because it renders text."""
    if not TOLERANCE_NOTES:
        return ""
    parts = [
        (
            f"{note.template} instant {note.index}: delta={note.max_delta}/255, "
            f"{note.changed} px ({note.changed * 100.0 / note.total:.5f}%), "
            f"engine {note.engine_version or 'unknown'}"
        )
        for note in TOLERANCE_NOTES
    ]
    return (
        f", {len(TOLERANCE_NOTES)} within antialias tolerance ("
        + "; ".join(parts)
        + ")"
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
        prove_tolerance(Path(judgement_tmp))
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
        f"{IMAGES} images compared{_tolerance_note()}{_drift_note()}, "
        f"{elapsed:.1f}s (ceiling {CEILING_SECONDS:.0f}s)"
    )
    if slow:
        print(f"FAIL: suite took {elapsed:.1f}s, above the ceiling")
        return 1
    return 0 if FAIL == 0 and not slow else 1


if __name__ == "__main__":
    raise SystemExit(main())
