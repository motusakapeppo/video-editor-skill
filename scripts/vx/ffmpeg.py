"""vx.ffmpeg — binary discovery + safe subprocess execution."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .contract import MissingDependency

CREATE_NO_WINDOW = 0x08000000

_WINGET = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links"


def _find(name: str) -> str:
    from . import config

    configured = config.get(name)
    if configured and Path(str(configured)).exists():
        return str(configured)
    env = os.environ.get(f"VX_{name.upper()}")
    if env and Path(env).exists():
        return env
    found = shutil.which(name)
    if found:
        return found
    candidate = _WINGET / f"{name}.exe"
    if candidate.exists():
        return str(candidate)
    raise MissingDependency(
        f"{name} not found. Install it (winget install Gyan.FFmpeg), or set VX_{name.upper()}, "
        f"or add the '{name}' key to vx.config.json."
    )


@functools.lru_cache(maxsize=1)
def ffmpeg() -> str:
    return _find("ffmpeg")


@functools.lru_cache(maxsize=1)
def ffprobe() -> str:
    return _find("ffprobe")


@dataclass
class Proc:
    rc: int
    stdout: str
    stderr: str
    args: List[str]

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def tail(self, lines: int = 25) -> str:
        return "\n".join(self.stderr.strip().splitlines()[-lines:])


def run(args: Iterable[str], *, timeout: int = 3600) -> Proc:
    """Run a command as an argument list. Never uses a shell."""
    argv = [str(a) for a in args]
    creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return Proc(
            rc=124, stdout="", stderr=f"timeout after {timeout}s: {exc}", args=argv
        )
    return Proc(proc.returncode, proc.stdout or "", proc.stderr or "", argv)


def run_ff(args: Iterable[str], *, timeout: int = 3600, loglevel: str = "info") -> Proc:
    """Run ffmpeg with the house flags. loglevel defaults to info on purpose:
    silencedetect writes its findings at info level and -loglevel error hides them."""
    base = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel]
    return run(base + [str(a) for a in args], timeout=timeout)


def run_fp(args: Iterable[str]) -> Proc:
    return run(
        [ffprobe(), "-hide_banner", "-loglevel", "error"] + [str(a) for a in args]
    )


@functools.lru_cache(maxsize=1)
def capabilities() -> dict:
    """Parse the local ffmpeg build: filters, encoders, and the muxers we need."""
    filters = run_ff(["-filters"], loglevel="error")
    names = set()
    for line in filters.stdout.splitlines() + filters.stderr.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) <= 3:
            names.add(parts[1])
    encoders = run_ff(["-encoders"], loglevel="error")
    enc = set()
    for line in encoders.stdout.splitlines() + encoders.stderr.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            enc.add(parts[1])
    wanted = [
        "vidstabdetect",
        "vidstabtransform",
        "silencedetect",
        "silenceremove",
        "loudnorm",
        "ebur128",
        "astats",
        "ametadata",
        "scdet",
        "freezedetect",
        "subtitles",
        "ass",
        "concat",
        "trim",
        "atrim",
        "acrossfade",
        "afade",
        "crop",
        "scale",
        "zoompan",
        "boxblur",
        "gblur",
        "overlay",
        "afftdn",
        "eq",
        "curves",
        "colorbalance",
        "select",
        "metadata",
        "showinfo",
        "aselect",
        "volumedetect",
        "adeclip",
        "acompressor",
        "highpass",
        "split",
        "format",
        "setsar",
        "fps",
        "tpad",
        "apad",
        "anullsrc",
    ]
    caps = {name: name in names for name in wanted}
    caps["libx264"] = "libx264" in enc
    caps["aac"] = "aac" in enc
    caps["version"] = version()
    return caps


@functools.lru_cache(maxsize=1)
def version() -> str:
    proc = run([ffmpeg(), "-version"])
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    return first.replace("ffmpeg version ", "").split()[0] if first else "unknown"


def require(cap: str) -> None:
    caps = capabilities()
    if cap in caps and caps[cap] is False:
        raise MissingDependency(
            f"ffmpeg build lacks '{cap}'. Needed for this treatment."
        )
