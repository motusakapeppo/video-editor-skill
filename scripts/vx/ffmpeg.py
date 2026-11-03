"""vx.ffmpeg — binary discovery + safe subprocess execution."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .contract import MissingDependency, VxError

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


class ProcessTimeout(VxError):
    """A child process outlived its declared ceiling; it was killed and reaped."""

    def __init__(self, payload: Dict[str, object]) -> None:
        self.payload = dict(payload)
        super().__init__(
            f"{self.payload.get('step')} timed out after "
            f"{self.payload.get('timeout_s')}s: {self.payload.get('cmd_head')}"
        )


def _timeout_seconds(step: str, timeout: Optional[int]) -> int:
    from . import settings

    return (
        settings.clamp_timeout(timeout)
        if timeout is not None
        else settings.timeout_for(step)
    )


def _run_with_timeout(argv: List[str], timeout: int, step: str) -> Proc:
    from . import resources

    creationflags = resources.child_creation_flags()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # The kill is already in flight; the process is reaped by the OS and
            # the caller must still receive the original timeout, never a hang.
            pass
        payload = {
            "step": step,
            "timeout_s": timeout,
            "cmd_head": " ".join(argv[:4]),
        }
        raise ProcessTimeout(payload) from exc
    return Proc(proc.returncode, stdout or "", stderr or "", argv)


def run(
    args: Iterable[str], *, timeout: Optional[int] = None, step: str = "render"
) -> Proc:
    """Run a command as an argument list. Never uses a shell, never outlives its ceiling."""
    argv = [str(a) for a in args]
    return _run_with_timeout(argv, _timeout_seconds(step, timeout), step)


def run_ff(
    args: Iterable[str],
    *,
    timeout: Optional[int] = None,
    loglevel: str = "info",
    step: str = "render",
) -> Proc:
    """Run ffmpeg with the house flags. loglevel defaults to info on purpose:
    silencedetect writes its findings at info level and -loglevel error hides them."""
    from . import resources

    base = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel]
    # Thread espliciti solo quando il chiamante li dichiara (i worker di misura):
    # un valore scelto dal figlio non e' un valore lasciato al caso.
    threads = resources.ffmpeg_threads()
    if threads:
        base += ["-threads", str(threads)]
    return run(base + [str(a) for a in args], timeout=timeout, step=step)


def run_fp(args: Iterable[str]) -> Proc:
    return run(
        [ffprobe(), "-hide_banner", "-loglevel", "error"] + [str(a) for a in args],
        step="probe",
    )


@functools.lru_cache(maxsize=1)
def capabilities() -> dict:
    """Parse the local ffmpeg build: filters, encoders, and the muxers we need."""
    filters = run_ff(["-filters"], loglevel="error", step="probe")
    names = set()
    for line in filters.stdout.splitlines() + filters.stderr.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) <= 3:
            names.add(parts[1])
    encoders = run_ff(["-encoders"], loglevel="error", step="probe")
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
    proc = run([ffmpeg(), "-version"], step="probe")
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    return first.replace("ffmpeg version ", "").split()[0] if first else "unknown"


def require(cap: str) -> None:
    caps = capabilities()
    if cap in caps and caps[cap] is False:
        raise MissingDependency(
            f"ffmpeg build lacks '{cap}'. Needed for this treatment."
        )
