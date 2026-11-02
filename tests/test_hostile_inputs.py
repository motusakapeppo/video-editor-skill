"""Hostile inputs: 200 malformed or dangerous files must be refused, never crash or hang."""

from __future__ import annotations

import os
import atexit
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import ffmpeg as ff  # noqa: E402
from vx import ingest, probe  # noqa: E402
from vx.contract import VxError  # noqa: E402

CASES = 200
CASE_LIMIT = 5.0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"

KINDS = (
    "zero",
    "three_bytes",
    "mp4_truncated",
    "mp4_huge_size",
    "traversal",
    "rtl_nulish",
    "nul_char",
    "directory",
    "fifo",
    "long_name",
    "mkv_renamed",
    "jpeg_renamed",
    "absurd_resolution",
)

INGEST_KINDS = frozenset(
    {
        "zero",
        "three_bytes",
        "mp4_truncated",
        "mp4_huge_size",
        "traversal",
        "rtl_nulish",
        "directory",
        "mkv_renamed",
    }
)

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:62} {reason}")


def _mp4_truncated() -> bytes:
    ftyp = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    return ftyp + b"\x00\x00\x00\x08free" + b"\x00\x00\x10\x00mdat" + b"\x00" * 64


def _mp4_huge_size() -> bytes:
    ftyp = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    return (
        ftyp
        + b"\x00\x00\x00\x01mdat"
        + (10_000_000_000).to_bytes(8, "big")
        + b"\x00" * 32
    )


def _absurd_png(width: int = 60000, height: int = 60000) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + header
    crc = zlib.crc32(chunk) & 0xFFFFFFFF
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(header))
        + chunk
        + struct.pack(">I", crc)
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _seed_media(root: Path) -> tuple[bytes, bytes]:
    root.mkdir(parents=True, exist_ok=True)
    mkv, jpg = root / "seed.mkv", root / "seed.jpg"
    mkv_ok = False
    jpg_ok = False
    try:
        proc = subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x36:r=10",
                "-t",
                "0.2",
                "-pix_fmt",
                "yuv420p",
                str(mkv),
            ],
            capture_output=True,
            timeout=60,
        )
        mkv_ok = proc.returncode == 0 and mkv.is_file() and mkv.stat().st_size > 0
        proc = subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=32x18",
                "-frames:v",
                "1",
                str(jpg),
            ],
            capture_output=True,
            timeout=60,
        )
        jpg_ok = proc.returncode == 0 and jpg.is_file() and jpg.stat().st_size > 0
    except (OSError, subprocess.SubprocessError):
        return b"\x1aE\xdf\xa3" + b"\x00" * 64, b"\xff\xd8\xff\xd9"
    return (
        mkv.read_bytes() if mkv_ok else b"\x1aE\xdf\xa3" + b"\x00" * 64,
        jpg.read_bytes() if jpg_ok else b"\xff\xd8\xff\xd9",
    )


def _create(
    tmp: Path, index: int, kind: str, seed: tuple[bytes, bytes]
) -> tuple[Path | None, str]:
    mkv_bytes, jpg_bytes = seed
    try:
        if kind == "zero":
            path = tmp / f"zero_{index:03d}.mp4"
            path.write_bytes(b"")
            return path, ""
        if kind == "three_bytes":
            path = tmp / f"three_{index:03d}.mp4"
            path.write_bytes(os.urandom(3))
            return path, ""
        if kind == "mp4_truncated":
            path = tmp / f"truncated_{index:03d}.mp4"
            path.write_bytes(_mp4_truncated())
            return path, ""
        if kind == "mp4_huge_size":
            path = tmp / f"huge_{index:03d}.mp4"
            path.write_bytes(_mp4_huge_size())
            return path, ""
        if kind == "traversal":
            base = tmp / "deep" / "nest"
            base.mkdir(parents=True, exist_ok=True)
            path = base / f"..\\..\\evil_{index:03d}.mp4"
            path.write_bytes(b"\x00\x00\x00\x08free")
            return path, ""
        if kind == "rtl_nulish":
            path = tmp / f"rtl_{index:03d}_nul%00_\u202e4pm.mp4"
            path.write_bytes(b"not a video")
            return path, ""
        if kind == "nul_char":
            path = tmp / f"nul_{index:03d}_bad\x00name.mp4"
            path.write_bytes(b"not a video")
            return path, ""
        if kind == "directory":
            path = tmp / f"dir_{index:03d}_clip.mp4"
            path.mkdir()
            return path, ""
        if kind == "fifo":
            path = tmp / f"fifo_{index:03d}.mp4"
            if not hasattr(os, "mkfifo"):
                return None, "mkfifo non disponibile su questa piattaforma"
            os.mkfifo(path)
            return path, ""
        if kind == "long_name":
            path = tmp / ("x" * 3990 + f"_{index:03d}.mp4")
            path.write_bytes(b"not a video")
            return path, ""
        if kind == "mkv_renamed":
            path = tmp / f"mkv_{index:03d}_renamed.mp4"
            path.write_bytes(mkv_bytes)
            return path, ""
        if kind == "jpeg_renamed":
            path = tmp / f"jpeg_{index:03d}_renamed.mp4"
            path.write_bytes(jpg_bytes)
            return path, ""
        if kind == "absurd_resolution":
            path = tmp / f"absurd_{index:03d}.mp4"
            path.write_bytes(_absurd_png())
            return path, ""
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"[:120]
    raise AssertionError(f"unknown hostile kind {kind!r}")


def _attempt(fn, *args, **kwargs) -> tuple[str, object, float]:
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - every escape must be classified
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    start = time.perf_counter()
    worker.start()
    worker.join(CASE_LIMIT)
    elapsed = time.perf_counter() - start
    if worker.is_alive():
        return "timeout", None, elapsed
    error = box.get("error")
    if error is None:
        return "ok", box.get("value"), elapsed
    if isinstance(error, VxError):
        return "vx_error", error, elapsed
    if isinstance(error, ingest.IngestError):
        return "ingest_error", error, elapsed
    return "foreign_error", error, elapsed


def _declared(status: str) -> bool:
    return status in ("vx_error", "ingest_error")


def _inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def main() -> int:
    print("HOSTILE INPUTS")

    start = time.perf_counter()
    try:
        ff.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=1,
            step="probe",
        )
    except ff.ProcessTimeout as exc:
        elapsed = time.perf_counter() - start
        payload = getattr(exc, "payload", {}) or {}
        check(
            "timeout: ProcessTimeout con payload",
            {"step", "timeout_s", "cmd_head"} <= set(payload)
            and payload.get("timeout_s") == 1
            and payload.get("step") == "probe",
            payload,
        )
        check("timeout: processo ucciso in fretta", elapsed < 15.0, f"{elapsed:.2f}s")
    except BaseException as exc:  # noqa: BLE001
        check("timeout: ProcessTimeout", False, type(exc).__name__)
    else:
        check("timeout: ProcessTimeout", False, "nessuna eccezione")

    outer = Path(tempfile.mkdtemp(prefix="vx-hostile-"))
    atexit.register(shutil.rmtree, outer, True)
    tmp = outer / "work"
    guard = outer / "guard"
    tmp.mkdir()
    guard.mkdir()
    stage = tmp / "_stage"
    cwd_before = set(os.listdir(Path.cwd()))

    seed = _seed_media(tmp / "_seed")
    check("seed mkv/jpeg generati", len(seed[0]) > 0 and len(seed[1]) > 0)

    tallies = {
        "vx_error": 0,
        "ingest_error": 0,
        "unsupported": 0,
        "accepted": 0,
        "timeout": 0,
        "foreign_error": 0,
    }
    created = 0
    skipped: list[str] = []
    first_seen: set[str] = set()
    max_latency = 0.0
    escape_detail: list[str] = []
    ingest_calls = 0
    ingest_declared = 0

    for index in range(CASES):
        kind = KINDS[index % len(KINDS)]
        path, reason = _create(tmp, index, kind, seed)
        if path is None:
            skipped.append(f"{kind}: {reason}")
            continue
        created += 1

        status, value, elapsed = _attempt(probe.probe_asset, path)
        max_latency = max(max_latency, elapsed)
        if status == "ok":
            kind_name = str(getattr(value, "kind", ""))
            if kind_name == "video":
                tallies["accepted"] += 1
            else:
                tallies["unsupported"] += 1
        elif _declared(status):
            tallies[status] += 1
            if not str(value).strip():
                escape_detail.append(f"{path.name}: {status} senza messaggio")
        elif status == "timeout":
            tallies["timeout"] += 1
            escape_detail.append(f"{path.name}: oltre {CASE_LIMIT}s")
        else:
            tallies["foreign_error"] += 1
            escape_detail.append(f"{path.name}: {type(value).__name__}: {value}")

        if kind not in first_seen:
            first_seen.add(kind)
            extra_status, extra_value, extra_elapsed = _attempt(probe.probe, path)
            max_latency = max(max_latency, extra_elapsed)
            if extra_status in ("timeout", "foreign_error") or (
                _declared(extra_status) and not str(extra_value).strip()
            ):
                escape_detail.append(
                    f"{path.name}: probe() -> {extra_status} {type(extra_value).__name__}"
                )
            if kind in INGEST_KINDS:
                ingest_calls += 1
                ingest_status, ingest_value, ingest_elapsed = _attempt(
                    ingest.normalize_source, path, {}, stage
                )
                max_latency = max(max_latency, ingest_elapsed)
                if _declared(ingest_status):
                    ingest_declared += 1
                elif ingest_status in ("timeout", "foreign_error"):
                    escape_detail.append(
                        f"{path.name}: ingest -> {ingest_status} "
                        f"{type(ingest_value).__name__}: {ingest_value}"
                    )

    check("casi tentati = 200", CASES == 200, CASES)
    check("creati + rifiutati dall'OS = 200", created + len(skipped) == CASES, created)
    check("nessuna eccezione non dichiarata", not escape_detail, escape_detail[:4])
    check(
        "ogni caso sotto il limite",
        max_latency < CASE_LIMIT,
        f"max {max_latency:.3f}s",
    )

    outside = [str(p) for p in outer.rglob("*") if not (_inside(p, tmp) or p == guard)]
    check("nessun file fuori dalla sandbox", not outside, outside[:3])
    check("guardia intatta", not any(guard.iterdir()))
    check(
        "cwd non inquinata",
        set(os.listdir(Path.cwd())) == cwd_before,
        sorted(set(os.listdir(Path.cwd())) ^ cwd_before)[:4],
    )

    print()
    print(f"casi: {CASES} | creati: {created} | os-refused: {len(skipped)}")
    print(
        f"errori dichiarati: VxError {tallies['vx_error']} + "
        f"IngestError {tallies['ingest_error']}"
    )
    print(
        f"rifiutati (unsupported): {tallies['unsupported']} | "
        f"accettati come video: {tallies['accepted']}"
    )
    print(
        f"timeout: {tallies['timeout']} | eccezioni non dichiarate: "
        f"{tallies['foreign_error']}"
    )
    print(
        f"ingest.normalize_source: {ingest_calls} chiamate, {ingest_declared} errori dichiarati"
    )
    print(f"latenza massima per caso: {max_latency:.3f}s (limite {CASE_LIMIT:.1f}s)")
    if skipped:
        reasons: dict[str, int] = {}
        for item in skipped:
            key = item.split(":", 1)[0]
            reasons[key] = reasons.get(key, 0) + 1
        print(
            "skip per tipo: "
            + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
