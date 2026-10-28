"""vx.deliver_targets — what a profile is allowed to deliver, and what it costs.

Split out of :mod:`vx.deliver` for the same reason the encoder table deserves its
own file: *what* can be delivered is declaration, *how* it is produced is
process. A target name (``h264/mp4``) is the whole contract -- it names the
encoder, the codec, the container family, the pixel format and the audio codec
that the finished file must report, so a substitution can never be silent.

The probe is the second half of that contract: an encoder this ffmpeg build does
not carry turns into ``DELIVERY_ENCODER_MISSING`` naming it, never into a
quietly different encoder.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

from . import ffmpeg as ff
from .edl3 import EDLError

__all__ = [
    "Target",
    "TARGETS",
    "PROBED_ENCODERS",
    "targets",
    "spec",
    "encoders",
    "encoder_available",
    "missing",
]

#: The encoders F26-T07 asks about, printed as a probe table before any claim.
PROBED_ENCODERS = (
    "libx264",
    "libx265",
    "prores_ks",
    "dnxhd",
    "libsvtav1",
    "libaom-av1",
    "aac",
    "libopus",
)


@dataclass(frozen=True)
class Target:
    """The recipe for one declared target: what to encode and what to expect.

    ``name`` is the profile-facing string (``h264/mp4``); ``family`` is the
    container family the finished file must report, which is not the same as the
    muxer ffprobe names (mov and mp4 share ``mov,mp4,m4a,3gp,3g2,mj2``).
    """

    name: str
    encoder: str
    codec: str
    family: str
    pix_fmt: str
    ext: str
    audio_encoder: str
    audio_codec: str
    video_args: Tuple[str, ...] = ()


_H264 = ("-preset", "ultrafast", "-crf", "18")
_HEVC = ("-preset", "ultrafast", "-crf", "20", "-x265-params", "log-level=none")
_XDCAM = (
    "-b:v", "50M", "-minrate", "50M", "-maxrate", "50M", "-bufsize", "17825792",
    "-g", "12", "-bf", "2", "-dc", "10",
)

#: Every target name this build knows how to produce. A profile may only declare
#: a name from this table; anything else is refused, never approximated.
TARGETS: Dict[str, Target] = {
    "h264/mp4": Target("h264/mp4", "libx264", "h264", "mp4", "yuv420p", ".mp4", "aac", "aac", _H264),
    "h265/mp4": Target("h265/mp4", "libx265", "hevc", "mp4", "yuv420p", ".mp4", "aac", "aac", _HEVC),
    "hevc10/mp4": Target("hevc10/mp4", "libx265", "hevc", "mp4", "yuv420p10le", ".mp4", "aac", "aac", _HEVC),
    "prores422/mov": Target("prores422/mov", "prores_ks", "prores", "mov", "yuv422p10le", ".mov", "pcm_s24le", "pcm_s24le", ("-profile:v", "2")),
    "prores422hq/mov": Target("prores422hq/mov", "prores_ks", "prores", "mov", "yuv422p10le", ".mov", "pcm_s24le", "pcm_s24le", ("-profile:v", "3")),
    "dnxhr/mov": Target("dnxhr/mov", "dnxhd", "dnxhd", "mov", "yuv422p", ".mov", "pcm_s24le", "pcm_s24le", ("-profile:v", "dnxhr_hq")),
    "xdcam422/mxf": Target("xdcam422/mxf", "mpeg2video", "mpeg2video", "mxf", "yuv422p", ".mxf", "pcm_s24le", "pcm_s24le", _XDCAM),
}


def targets(profile: Any) -> Tuple[str, ...]:
    """The targets ``profile`` declares, in declaration order."""
    if isinstance(profile, str):
        from . import profiles  # noqa: PLC0415 - a name is resolved, never assumed

        profile = profiles.resolve(profile)
    declared = (
        profile.get("deliverables")
        if isinstance(profile, Mapping)
        else getattr(profile, "deliverables", None)
    )
    return tuple(str(item) for item in (declared or ()))


def spec(target: str) -> Target:
    """The recipe for ``target``; an unknown name is refused with its table."""
    key = str(target).strip()
    found = TARGETS.get(key)
    if found is None:
        raise EDLError(
            "DELIVERY_UNKNOWN_TARGET",
            f"no delivery recipe for {key!r}; recipes are {sorted(TARGETS)}",
            "deliverables",
        )
    return found


@functools.lru_cache(maxsize=1)
def encoders() -> frozenset:
    """Every encoder name this ffmpeg build reports, probed once per process."""
    proc = ff.run_ff(["-encoders"], loglevel="error", step="probe")
    names = set()
    for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def encoder_available(name: str) -> bool:
    return str(name) in encoders()


def missing(profile: Any) -> List[Tuple[str, str]]:
    """``(target, encoder)`` for every declared target this build cannot encode."""
    return [
        (key, spec(key).encoder)
        for key in targets(profile)
        if not encoder_available(spec(key).encoder)
    ]
