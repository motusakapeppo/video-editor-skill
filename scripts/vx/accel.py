"""vx.accel — hardware routing decided by execution, never by a capability list.

`ffmpeg -hwaccels` and `-encoders` are advertisements: on this machine `h264_amf` is
listed and fails to load, while `h264_qsv` is listed and works. The only honest test is
to encode eight frames and look at the result. Same rule for Whisper: a device is
"available" only after a real transcribe completes inside a hard timeout — a hang is a
failure, not a slow success.

Routing policy (see `pick`):

  scratch / preview  -> first verified hardware encoder (speed wins, size does not)
  master / delivery  -> software encoder (bitrate and quality control win)
  explicit job flag  -> whatever the payload asks for, hardware included
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import contract
from . import ffmpeg as ff

CACHE_NAME = "accel.json"
TEST_FRAMES = 8

ENCODERS: Dict[str, Dict[str, Any]] = {
    "h264_nvenc": {
        "software": False,
        "codec": "h264",
        "args": [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "{quality}",
            "-b:v",
            "0",
        ],
    },
    "hevc_nvenc": {
        "software": False,
        "codec": "hevc",
        "args": [
            "-c:v",
            "hevc_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "{quality}",
            "-b:v",
            "0",
        ],
    },
    "h264_qsv": {
        "software": False,
        "codec": "h264",
        "args": [
            "-c:v",
            "h264_qsv",
            "-global_quality",
            "{quality}",
            "-look_ahead",
            "0",
        ],
    },
    "hevc_qsv": {
        "software": False,
        "codec": "hevc",
        "args": [
            "-c:v",
            "hevc_qsv",
            "-global_quality",
            "{quality}",
            "-look_ahead",
            "0",
        ],
    },
    "h264_amf": {
        "software": False,
        "codec": "h264",
        "args": [
            "-c:v",
            "h264_amf",
            "-quality",
            "balanced",
            "-rc",
            "cqp",
            "-qp_i",
            "{quality}",
            "-qp_p",
            "{quality}",
        ],
    },
    "libx264": {
        "software": True,
        "codec": "h264",
        "args": ["-c:v", "libx264", "-crf", "{quality}", "-preset", "{preset}"],
    },
    "libx265": {
        "software": True,
        "codec": "hevc",
        "args": ["-c:v", "libx265", "-crf", "{quality}", "-preset", "{preset}"],
    },
}

_VERIFIED: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# CUDA DLL discovery (pip puts them under site-packages/nvidia/*/bin)
# --------------------------------------------------------------------------- #


def cuda_dll_dirs() -> List[str]:
    roots = {
        sysconfig.get_paths().get("purelib", ""),
        sysconfig.get_paths().get("platlib", ""),
    }
    dirs: List[str] = []
    for root in filter(None, roots):
        for pattern in ("nvidia/*/bin", "nvidia/*/lib/x64"):
            dirs.extend(glob.glob(os.path.join(root, pattern)))
    return sorted(set(dirs))


def register_cuda_dlls() -> List[str]:
    """Make cuBLAS/cuDNN visible to CTranslate2 before it is imported."""
    dirs = cuda_dll_dirs()
    for path in dirs:
        try:
            os.add_dll_directory(path)
        except (AttributeError, OSError):
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    return dirs


# --------------------------------------------------------------------------- #
# probes that execute
# --------------------------------------------------------------------------- #


def test_encode(
    name: str, *, quality: int = 24, preset: str = "veryfast", frames: int = TEST_FRAMES
) -> Dict[str, Any]:
    spec = ENCODERS.get(name)
    if spec is None:
        return {"ok": False, "error": "unknown encoder", "ms": 0}
    out = Path(tempfile.mkdtemp(prefix="vxenc-")) / f"{name}.mp4"
    args = [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30:duration=1",
        "-frames:v",
        str(frames),
        *[str(part).format(quality=quality, preset=preset) for part in spec["args"]],
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]
    started = time.time()
    proc = ff.run_ff(args, loglevel="error", timeout=120)
    elapsed = (time.time() - started) * 1000
    ok = proc.ok and out.exists() and out.stat().st_size > 1024
    error = "" if ok else (proc.tail(3) or "empty output")
    if out.exists():
        out.unlink(missing_ok=True)
    return {"ok": bool(ok), "error": error[:300], "ms": round(elapsed, 1)}


def probe_encoders(*, names: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    listed = set()
    proc = ff.run_ff(["-encoders"], loglevel="error")
    for line in (proc.stdout + proc.stderr).splitlines():
        parts = line.split()
        if (
            len(parts) >= 2
            and parts[0] == "V....D"
            or (len(parts) >= 2 and parts[0].startswith("V"))
        ):
            listed.add(parts[1])
    results: Dict[str, Dict[str, Any]] = {}
    for name in names or list(ENCODERS):
        info = {"listed": name in listed, "ok": False, "error": "", "ms": 0.0}
        if info["listed"]:
            info.update(test_encode(name))
        else:
            info["error"] = "not built into this ffmpeg"
        results[name] = info
    return results


def probe_whisper(*, timeout: float = 180.0) -> Dict[str, Any]:
    """Ask a child process to actually transcribe on each device. A hang is a failure."""
    attempts = [("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")]
    findings: List[Dict[str, Any]] = []
    for device, compute in attempts:
        started = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "vx.accel", "--whisper-child", device, compute],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            elapsed = time.time() - started
            ok = proc.returncode == 0 and "WORDS=" in proc.stdout
            findings.append(
                {
                    "device": device,
                    "compute_type": compute,
                    "ok": ok,
                    "seconds": round(elapsed, 2),
                    "reason": ""
                    if ok
                    else (
                        proc.stderr.strip().splitlines()[-1][:180]
                        if proc.stderr.strip()
                        else f"rc={proc.returncode}"
                    ),
                }
            )
            if ok:
                return {
                    "device": device,
                    "compute_type": compute,
                    "verified": True,
                    "attempts": findings,
                    "reason": "",
                }
        except subprocess.TimeoutExpired:
            findings.append(
                {
                    "device": device,
                    "compute_type": compute,
                    "ok": False,
                    "seconds": round(time.time() - started, 2),
                    "reason": f"hung beyond {timeout}s",
                }
            )
    return {
        "device": "cpu",
        "compute_type": "int8",
        "verified": True,
        "attempts": findings,
        "reason": "no GPU path completed; CPU int8 fallback",
    }


def gpu_summary() -> Dict[str, Any]:
    info: Dict[str, Any] = {"smi": False, "name": "", "driver": "", "memory_mib": 0}
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            name, driver, mem = [
                part.strip() for part in proc.stdout.strip().splitlines()[0].split(",")
            ]
            info.update(
                {
                    "smi": True,
                    "name": name,
                    "driver": driver,
                    "memory_mib": int(mem.split()[0]),
                }
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return info


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def cache_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    target = Path(base) / "vx" / CACHE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def probe(*, force: bool = False, ttl_hours: float = 24.0) -> Dict[str, Any]:
    global _VERIFIED
    if _VERIFIED is not None and not force:
        return _VERIFIED
    path = cache_path()
    if not force and path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            age_h = (time.time() - cached.get("generated_at", 0)) / 3600
            if age_h <= ttl_hours:
                _VERIFIED = cached
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    hw = ff.run_ff(["-hwaccels"], loglevel="error")
    report = {
        "schema": "vx.accel/1.0",
        "generated_at": time.time(),
        "ffmpeg": ff.version(),
        "hwaccels": [
            line.strip()
            for line in (hw.stdout + hw.stderr).splitlines()[1:]
            if line.strip()
        ],
        "gpu": gpu_summary(),
        "encoders": probe_encoders(),
        "whisper": probe_whisper(),
        "cuda_dll_dirs": len(cuda_dll_dirs()),
    }
    report["best_scratch"] = next(
        (
            name
            for name, info in report["encoders"].items()
            if info.get("ok") and not ENCODERS[name]["software"]
        ),
        "",
    )
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _VERIFIED = report
    return report


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


def _args_for(name: str, *, quality: int, preset: str) -> List[str]:
    spec = ENCODERS[name]
    return [str(part).format(quality=quality, preset=preset) for part in spec["args"]]


def pick(
    kind: str = "master",
    *,
    quality: Optional[int] = None,
    preset: str = "medium",
    preferred: str = "",
) -> Dict[str, Any]:
    """Return the encoder to use for `kind` ('scratch' | 'master'), with its arguments."""
    from . import settings

    cfg = settings.current().get("accel") or {}
    ttl = float(cfg.get("probe_ttl_hours", 24))
    verified = probe(ttl_hours=ttl)
    encoders = verified.get("encoders") or {}

    if preferred:
        info = encoders.get(preferred) or {}
        if info.get("ok"):
            codec_quality = (
                quality if quality is not None else (21 if kind == "scratch" else 18)
            )
            return {
                "name": preferred,
                "software": ENCODERS[preferred]["software"],
                "args": _args_for(preferred, quality=codec_quality, preset=preset),
                "verified": True,
                "reason": "explicitly requested",
            }
        return pick("master", quality=quality, preset=preset, preferred="")

    if kind == "scratch":
        best = verified.get("best_scratch") or ""
        if best:
            cq = int(quality if quality is not None else cfg.get("nvenc_cq", 21))
            return {
                "name": best,
                "software": False,
                "args": _args_for(best, quality=cq, preset=preset),
                "verified": True,
                "reason": "verified hardware encoder for scratch/preview work",
            }
        return {
            "name": "libx264",
            "software": True,
            "args": _args_for(
                "libx264",
                quality=int(quality if quality is not None else 23),
                preset="veryfast",
            ),
            "verified": bool(encoders.get("libx264", {}).get("ok")),
            "reason": "no verified hardware encoder; software fallback",
        }

    master = str(cfg.get("master_encoder") or "libx264")
    if master == "auto" or not (encoders.get(master) or {}).get("ok"):
        master = "libx264"
    return {
        "name": master,
        "software": ENCODERS.get(master, {}).get("software", True),
        "args": _args_for(
            master, quality=int(quality if quality is not None else 18), preset=preset
        ),
        "verified": bool((encoders.get(master) or {}).get("ok")),
        "reason": "software encoder for delivery: bitrate and quality control",
    }


def whisper_target(*, preferred: str = "") -> Dict[str, Any]:
    from . import settings

    cfg = settings.current().get("accel") or {}
    if preferred == "cpu":
        return {
            "device": "cpu",
            "compute_type": "int8",
            "verified": True,
            "reason": "requested",
        }
    if preferred == "cuda":
        register_cuda_dlls()
        return {
            "device": "cuda",
            "compute_type": "float16",
            "verified": False,
            "reason": "requested, unverified",
        }
    verified = (
        probe(ttl_hours=float(cfg.get("probe_ttl_hours", 24))).get("whisper") or {}
    )
    if not verified:
        register_cuda_dlls()
        return {
            "device": "cpu",
            "compute_type": "int8",
            "verified": True,
            "reason": "probe unavailable",
        }
    if verified.get("device") == "cuda":
        register_cuda_dlls()
    return verified


def describe() -> Dict[str, Any]:
    verified = probe()
    return {
        "ffmpeg": verified.get("ffmpeg"),
        "hwaccels": verified.get("hwaccels"),
        "gpu": verified.get("gpu"),
        "encoders": {
            name: {
                "listed": info.get("listed"),
                "ok": info.get("ok"),
                "ms": info.get("ms"),
            }
            for name, info in (verified.get("encoders") or {}).items()
        },
        "best_scratch": verified.get("best_scratch"),
        "whisper": verified.get("whisper"),
        "scratch_choice": pick("scratch")["name"],
        "master_choice": pick("master")["name"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 3 and args[0] == "--whisper-child":
        register_cuda_dlls()
        media = os.environ.get("VX_PROBE_MEDIA", "")
        if not media or not Path(media).is_file():
            media = str(_probe_media())
        try:
            from faster_whisper import WhisperModel  # type: ignore

            engine = WhisperModel(
                os.environ.get("VX_PROBE_MODEL", "tiny"),
                device=args[1],
                compute_type=args[2],
            )
            segments, _ = engine.transcribe(
                media, beam_size=1, word_timestamps=False, vad_filter=False
            )
            count = sum(1 for _ in segments)
            print(f"WORDS={count} DEVICE={args[1]}/{args[2]}")
            return 0
        except Exception as exc:  # noqa: BLE001 - the whole point is to report it
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(describe(), ensure_ascii=False, indent=1, default=str))
    return 0


def _probe_media() -> Path:
    """Deterministic 3 s signal loud enough that the encoder really runs."""
    target = Path(tempfile.mkdtemp(prefix="vxprobe-")) / "probe.wav"
    ff.run_ff(
        [
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=pink:amplitude=0.3:duration=3",
            "-af",
            "volume=2.0",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        loglevel="error",
        timeout=60,
    )
    return target


if __name__ == "__main__":
    raise SystemExit(main())
