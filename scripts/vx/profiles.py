"""vx.profiles — the declared genre profiles (F26-T01, D033).

The engine is universal; the director is per profile. Nothing in the renderer,
the compiler or the effect registry knows a reel from a feature: a *profile* is
this module's data, and a document points at one row with
``Timeline3.profile_ref``. Adding a genre is adding a row and its proof, never a
branch (D033 #1, #2, #3).

Every number below carries its standard in ``notes``, because a loudness target
without a citation is a guess. The columns are the ones a director actually
chooses with: canvas and rate, length window, loudness and true peak, colour,
deliverables, and the structural grammar (hook, beat, interviews, acts, cut
density, subtitles).

Import-cheap on purpose: no numpy, no ffmpeg, no filesystem at import. The only
import is :class:`vx.edl3.EDLError`, the stable refusal-code carrier.

Refusal
-------
:func:`resolve` raises ``EDLError("PROFILE_UNKNOWN", ...)`` naming every
declared id when the reference does not resolve, and ``Timeline3.validate()``
turns that into the ``PROFILE_UNKNOWN`` validation code, which the compiler
treats as fatal (``edl3_render.HARD_CODES``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from .edl3 import EDLError

__all__ = [
    "FPS_CHOICES",
    "TRANSFERS",
    "SUBTITLE_MODES",
    "BIT_DEPTHS",
    "Profile",
    "PROFILES",
    "names",
    "resolve",
    "check",
]

#: Frame rates a declared profile may use. The list is the delivery reality, not
#: a preference: 23.976/24/25/29.97/30/50/60 are the rates a camera or an
#: encoder actually produces.
FPS_CHOICES = frozenset({23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 60.0})
#: Transfer functions a profile may declare. SDR is the default; an HDR profile
#: names HLG (broadcast, BT.2100) or PQ (HDR10, BT.2100) explicitly.
TRANSFERS = frozenset({"sdr", "hlg", "pq"})
#: How subtitles leave the house for this genre.
SUBTITLE_MODES = frozenset({"burned", "sidecar", "none"})
#: Delivery bit depths. 8 bit is the SDR deliverable; 10 bit is the graded
#: negative (or an HDR deliverable) and is never silently up-converted.
BIT_DEPTHS = (8, 10)


@dataclass(frozen=True)
class Profile:
    """One declared genre: what it looks like, sounds like, and is built like.

    ``cut_density_min``/``cut_density_max`` are cuts per ten seconds, the unit
    D031's density rule already uses. ``act_count`` is ``0`` when the genre has
    no act structure (a tutorial is a sequence of steps, not three acts).
    ``notes`` is one sentence saying where the numbers come from.
    """

    id: str
    label: str
    width: int
    height: int
    fps: float
    min_seconds: float
    max_seconds: float
    loudness_lufs: float
    true_peak_db: float
    bit_depth: int
    transfer: str
    deliverables: Tuple[str, ...]
    hook_seconds: float
    beat_driven: bool
    interview_driven: bool
    act_count: int
    cut_density_min: float
    cut_density_max: float
    subtitles: str
    notes: str


#: The registry, in declaration order (``names()`` returns it unchanged). The
#: order is genre familiarity, not precedence: nothing resolves by position.
PROFILES: Dict[str, Profile] = {
    "reel": Profile(
        id="reel",
        label="Reel verticale",
        width=1080,
        height=1920,
        fps=30.0,
        min_seconds=15.0,
        max_seconds=90.0,
        loudness_lufs=-14.0,
        true_peak_db=-1.0,
        bit_depth=8,
        transfer="sdr",
        deliverables=("h264/mp4",),
        hook_seconds=3.0,
        beat_driven=True,
        interview_driven=False,
        act_count=0,
        cut_density_min=6.0,
        cut_density_max=12.0,
        subtitles="burned",
        notes=(
            "-14 LUFS and -1 dBTP are the streaming-platform target every social "
            "upload is normalised to; 1080x1920 at 30 fps is the vertical canvas "
            "Reels and TikTok accept, and 15-90 s is the length window they allow."
        ),
    ),
    "short": Profile(
        id="short",
        label="Short verticale",
        width=1080,
        height=1920,
        fps=30.0,
        min_seconds=15.0,
        max_seconds=60.0,
        loudness_lufs=-14.0,
        true_peak_db=-1.0,
        bit_depth=8,
        transfer="sdr",
        deliverables=("h264/mp4",),
        hook_seconds=3.0,
        beat_driven=True,
        interview_driven=False,
        act_count=0,
        cut_density_min=6.0,
        cut_density_max=14.0,
        subtitles="burned",
        notes=(
            "-14 LUFS and -1 dBTP are the same platform target as a reel, but "
            "Shorts caps the length at 60 s and its grammar cuts faster: 6-14 "
            "cuts per 10 s against the reel's 6-12."
        ),
    ),
    "videoclip": Profile(
        id="videoclip",
        label="Videoclip musicale",
        width=1920,
        height=1080,
        fps=24.0,
        min_seconds=120.0,
        max_seconds=300.0,
        loudness_lufs=-14.0,
        true_peak_db=-1.0,
        bit_depth=8,
        transfer="sdr",
        deliverables=("h264/mp4", "prores422/mov"),
        hook_seconds=0.0,
        beat_driven=True,
        interview_driven=False,
        act_count=0,
        cut_density_min=4.0,
        cut_density_max=12.0,
        subtitles="none",
        notes=(
            "24 fps is the film-musical rate and -14 LUFS the streaming-platform "
            "target the track is mastered to, so the clip plays at the loudness of "
            "the release; 120-300 s is the song length."
        ),
    ),
    "documentario": Profile(
        id="documentario",
        label="Documentario",
        width=1920,
        height=1080,
        fps=25.0,
        min_seconds=600.0,
        max_seconds=5400.0,
        loudness_lufs=-16.0,
        true_peak_db=-1.5,
        bit_depth=8,
        transfer="sdr",
        deliverables=("h264/mp4",),
        hook_seconds=0.0,
        beat_driven=False,
        interview_driven=True,
        act_count=0,
        cut_density_min=1.0,
        cut_density_max=4.0,
        subtitles="sidecar",
        notes=(
            "-16 LUFS is this profile's web/speech target (AES TD1008 streaming "
            "recommendation), which is what a web-first documentary is mixed to; a "
            "broadcast commission wants -23 LUFS (EBU R128) instead, and 25 fps is "
            "the European acquisition rate."
        ),
    ),
    "film": Profile(
        id="film",
        label="Film narrativo",
        width=2048,
        height=858,
        fps=24.0,
        min_seconds=4800.0,
        max_seconds=10800.0,
        loudness_lufs=-24.0,
        true_peak_db=-2.0,
        bit_depth=10,
        transfer="sdr",
        deliverables=("prores422hq/mov", "h265/mp4"),
        hook_seconds=0.0,
        beat_driven=False,
        interview_driven=False,
        act_count=3,
        cut_density_min=0.5,
        cut_density_max=3.0,
        subtitles="sidecar",
        notes=(
            "-24 LUFS and -2 dBTP are ATSC A/85, the feature/broadcast delivery "
            "target; 2048x858 is the DCI 2K scope raster, 10 bit keeps a graded "
            "negative from banding, and three acts are the feature grammar."
        ),
    ),
    "spot": Profile(
        id="spot",
        label="Spot pubblicitario",
        width=1920,
        height=1080,
        fps=25.0,
        min_seconds=10.0,
        max_seconds=60.0,
        loudness_lufs=-23.0,
        true_peak_db=-1.0,
        bit_depth=8,
        transfer="sdr",
        deliverables=("xdcam422/mxf", "h264/mp4"),
        hook_seconds=2.0,
        beat_driven=False,
        interview_driven=False,
        act_count=0,
        cut_density_min=3.0,
        cut_density_max=8.0,
        subtitles="none",
        notes=(
            "-23 LUFS integrated and -1 dBTP are EBU R128, the European broadcast "
            "delivery target; 25 fps is the European broadcast rate and 10-60 s is "
            "the commercial-break slot."
        ),
    ),
    "tutorial": Profile(
        id="tutorial",
        label="Tutorial",
        width=1920,
        height=1080,
        fps=30.0,
        min_seconds=120.0,
        max_seconds=1800.0,
        loudness_lufs=-16.0,
        true_peak_db=-1.5,
        bit_depth=8,
        transfer="sdr",
        deliverables=("h264/mp4",),
        hook_seconds=5.0,
        beat_driven=False,
        interview_driven=False,
        act_count=0,
        cut_density_min=0.5,
        cut_density_max=3.0,
        subtitles="none",
        notes=(
            "-16 LUFS is the web speech target (AES TD1008); 0.5-3 cuts per 10 s is "
            "a screen recording that holds a shot long enough to follow, and 30 fps "
            "keeps UI motion legible."
        ),
    ),
    "evento": Profile(
        id="evento",
        label="Evento lungo",
        width=1920,
        height=1080,
        fps=25.0,
        min_seconds=1800.0,
        max_seconds=14400.0,
        loudness_lufs=-23.0,
        true_peak_db=-1.0,
        bit_depth=8,
        transfer="sdr",
        deliverables=("h264/mp4", "h265/mp4"),
        hook_seconds=0.0,
        beat_driven=False,
        interview_driven=False,
        act_count=0,
        cut_density_min=0.5,
        cut_density_max=2.0,
        subtitles="none",
        notes=(
            "-23 LUFS and -1 dBTP are EBU R128, the venue and broadcast feed "
            "target; 30-240 min is a whole event, so the cut density stays under 2 "
            "per 10 s, and 25 fps matches the multicam hardware."
        ),
    ),
    "trailer": Profile(
        id="trailer",
        label="Trailer",
        width=2048,
        height=858,
        fps=24.0,
        min_seconds=90.0,
        max_seconds=180.0,
        loudness_lufs=-23.0,
        true_peak_db=-1.0,
        bit_depth=10,
        transfer="sdr",
        deliverables=("prores422hq/mov", "h264/mp4"),
        hook_seconds=5.0,
        beat_driven=True,
        interview_driven=False,
        act_count=3,
        cut_density_min=4.0,
        cut_density_max=10.0,
        subtitles="none",
        notes=(
            "-23 LUFS and -1 dBTP are EBU R128 for cinema/broadcast pre-roll; "
            "2048x858 is the DCI 2K scope raster, 10 bit keeps the trailer grade, "
            "and three acts at 4-10 cuts per 10 s are the trailer grammar."
        ),
    ),
}


def names() -> Tuple[str, ...]:
    """Every declared profile id, in declaration order."""
    return tuple(PROFILES)


def resolve(name: str) -> Profile:
    """The profile called ``name``; an unknown name is refused with its ids.

    The refusal names every declared id, so a typo is fixed from the message
    alone and never from reading this module.
    """
    key = str(name).strip()
    profile = PROFILES.get(key)
    if profile is None:
        raise EDLError(
            "PROFILE_UNKNOWN",
            f"no genre profile {key!r}; declared profiles are {sorted(PROFILES)}",
            "profile_ref",
        )
    return profile


def check(profile: Profile) -> Tuple[str, ...]:
    """Return one human problem per invalid field; empty when the row is sane.

    This is data validation, not document validation: it answers "is this row
    usable as a declaration at all", so a hand-edited profile is refused before
    a document can point at it. ``Timeline3.validate()`` only asks whether the
    *reference* resolves, and reports ``PROFILE_UNKNOWN`` when it does not.
    """
    problems = []

    def add(field: str, detail: str) -> None:
        problems.append(f"{profile.id}.{field}: {detail}")

    if profile.fps not in FPS_CHOICES:
        add("fps", f"{profile.fps:g} is not a deliverable rate; declared are {sorted(FPS_CHOICES)}")
    if profile.width <= 0 or profile.height <= 0:
        add("canvas", f"{profile.width}x{profile.height} is not a canvas")
    if profile.min_seconds > profile.max_seconds:
        add(
            "min_seconds",
            f"{profile.min_seconds:g}s is above max_seconds {profile.max_seconds:g}s",
        )
    if profile.loudness_lufs >= 0.0:
        add("loudness_lufs", f"{profile.loudness_lufs:g} LUFS must be negative")
    if profile.true_peak_db > 0.0:
        add("true_peak_db", f"{profile.true_peak_db:g} dBTP must be <= 0")
    if profile.bit_depth not in BIT_DEPTHS:
        add("bit_depth", f"{profile.bit_depth} is not one of {list(BIT_DEPTHS)}")
    if profile.transfer not in TRANSFERS:
        add("transfer", f"{profile.transfer!r} is not one of {sorted(TRANSFERS)}")
    if not profile.deliverables:
        add("deliverables", "no codec/container is declared")
    if profile.hook_seconds > profile.min_seconds:
        add(
            "hook_seconds",
            f"a {profile.hook_seconds:g}s hook does not fit a {profile.min_seconds:g}s "
            "minimum length",
        )
    if profile.cut_density_min > profile.cut_density_max:
        add(
            "cut_density_min",
            f"{profile.cut_density_min:g} is above cut_density_max "
            f"{profile.cut_density_max:g}",
        )
    if profile.act_count < 0:
        add("act_count", f"{profile.act_count} acts is not a structure")
    if profile.subtitles not in SUBTITLE_MODES:
        add("subtitles", f"{profile.subtitles!r} is not one of {sorted(SUBTITLE_MODES)}")
    return tuple(problems)
