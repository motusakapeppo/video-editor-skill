"""vx.edl_plan — EDL v2.0 multi-source model (Phase 3.1).

Pure data model: stdlib only, no imports from ``vx.*``. The single-source
``Edl`` in :mod:`vx.edl` re-exports these names at the bottom of that module,
so ``vx.edl.EDLPlan`` and ``vx.edl_plan.EDLPlan`` are the same class.

The wire format is the nested v2.0 shape: contract_version, timeline, sources,
tracks.video (always ``clips``) and tracks.audio (flattened when the track has
exactly one clip, because that is the compact form of the contract; ``clips``
is used as soon as a track carries more than one).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

EDL_SCHEMA = "vx.edl/2.2"
EDL_CONTRACT_VERSION = "2.2.0"

_SOURCE_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ASPECT = re.compile(r"^(\d+):(\d+)$")

_TOP_KEYS = {"contract_version", "timeline", "sources", "tracks", "ducking"}
_TIMELINE_KEYS = {"fps", "duration_sec", "aspect_ratio", "resolution"}
_SOURCE_KEYS = {"path", "sha256", "duration", "camera"}
_CAMERA_KEYS = {"angle_deg", "focal_length_mm"}
_TRACK_KEYS = {"track_id", "role", "clips"}
_AUDIO_KEYS = _TRACK_KEYS | {"source_id", "src_in", "src_out", "dst_in", "dst_out"}
_CLIP_KEYS = {
    "clip_id",
    "source_id",
    "src_in",
    "src_out",
    "dst_in",
    "dst_out",
    "layer",
    "role",
    "transition_in",
    "transition_out",
    "gain_db",
}
_TRANSITION_KEYS = {"type", "duration_sec"}
_DUCKING_KEYS = {
    "sidechain_role",
    "target_role",
    "threshold",
    "ratio",
    "attack_ms",
    "release_ms",
    "makeup_db",
}


class EDLValidationError(ValueError):
    """An EDL document was malformed; ``errors`` lists every path."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors: List[str] = [str(error) for error in errors]
        super().__init__("; ".join(self.errors) if self.errors else "invalid EDL plan")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _reject_unknown(data: Any, allowed: set, path: str, errors: List[str]) -> None:
    if not isinstance(data, dict):
        errors.append(f"{path or 'plan'}: must be an object")
        return
    for key in data:
        if key not in allowed:
            errors.append(f"{path}.{key}" if path else str(key))


class _FrozenMap(Mapping):
    """Read-only mapping that raises TypeError on every write, including ``__setitem__``."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = MappingProxyType(dict(data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("sources is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("sources is immutable")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self._data) == dict(other)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result

    __hash__ = None


@dataclass(frozen=True)
class CameraSpec:
    """Camera metadata. ``None`` means "not declared", never a guessed default."""

    angle_deg: Optional[float] = None
    focal_length_mm: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.angle_deg is not None:
            data["angle_deg"] = self.angle_deg
        if self.focal_length_mm is not None:
            data["focal_length_mm"] = self.focal_length_mm
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "CameraSpec":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("camera must be an object")
        angle = data.get("angle_deg")
        focal = data.get("focal_length_mm")
        return cls(
            angle_deg=None if angle is None else float(angle),
            focal_length_mm=None if focal is None else float(focal),
        )


@dataclass(frozen=True)
class SourceRef:
    path: str
    sha256: str
    duration: Optional[float] = None
    camera: CameraSpec = field(default_factory=CameraSpec)


@dataclass(frozen=True)
class Transition:
    type: str
    duration_sec: float


@dataclass(frozen=True)
class DuckingSpec:
    """Declared sidechain ducking. Ranges mirror ffmpeg 8.1 ``sidechaincompress``.

    ``makeup_db`` is non-negative because ffmpeg's linear ``makeup`` has a
    minimum of 1: a negative dB value cannot be expressed.
    """

    sidechain_role: str = "dialogue"
    target_role: str = "music"
    threshold: float = 0.05
    ratio: float = 8.0
    attack_ms: float = 20.0
    release_ms: float = 250.0
    makeup_db: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sidechain_role": self.sidechain_role,
            "target_role": self.target_role,
            "threshold": self.threshold,
            "ratio": self.ratio,
            "attack_ms": self.attack_ms,
            "release_ms": self.release_ms,
            "makeup_db": self.makeup_db,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DuckingSpec":
        if not isinstance(data, dict):
            raise ValueError("ducking must be an object")
        return cls(
            sidechain_role=data.get("sidechain_role", "dialogue"),
            target_role=data.get("target_role", "music"),
            threshold=float(data.get("threshold", 0.05)),
            ratio=float(data.get("ratio", 8.0)),
            attack_ms=float(data.get("attack_ms", 20.0)),
            release_ms=float(data.get("release_ms", 250.0)),
            makeup_db=float(data.get("makeup_db", 0.0)),
        )


@dataclass(frozen=True)
class ClipSegment:
    # clip_id is a label, not content: the flattened audio form cannot carry
    # it, so equality is defined on the segment itself. Uniqueness is checked
    # explicitly by EDLPlan.validate().
    clip_id: str = field(compare=False)
    source_id: str
    src_in: float
    src_out: float
    dst_in: float
    dst_out: float
    layer: int = 0
    role: str = "main_talking_head"
    transition_in: Optional[Transition] = None
    transition_out: Optional[Transition] = None
    gain_db: float = 0.0

    @property
    def duration(self) -> float:
        return float(self.dst_out) - float(self.dst_in)


@dataclass(frozen=True)
class Track:
    track_id: int
    clips: Tuple[ClipSegment, ...]
    role: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "clips", tuple(self.clips))


@dataclass(frozen=True)
class EDLPlan:
    contract_version: str = field(default=EDL_CONTRACT_VERSION, kw_only=True)
    fps: float
    duration_sec: float
    aspect_ratio: str
    resolution: Tuple[int, int]
    sources: Mapping[str, SourceRef]
    video_tracks: Tuple[Track, ...]
    audio_tracks: Tuple[Track, ...] = ()
    notes: Tuple[str, ...] = ()
    ducking: Optional[DuckingSpec] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", _FrozenMap(self.sources))
        object.__setattr__(self, "video_tracks", tuple(self.video_tracks))
        object.__setattr__(self, "audio_tracks", tuple(self.audio_tracks))
        object.__setattr__(self, "notes", tuple(self.notes))
        if isinstance(self.resolution, (list, tuple)):
            object.__setattr__(self, "resolution", tuple(self.resolution))

    # -- validation -------------------------------------------------------- #

    def validate(self) -> List[str]:
        errors: List[str] = []

        if self.contract_version != EDL_CONTRACT_VERSION:
            errors.append(
                f"contract_version must be {EDL_CONTRACT_VERSION!r}, "
                f"got {self.contract_version!r}"
            )
        if not _is_num(self.fps) or float(self.fps) <= 0:
            errors.append("timeline.fps must be > 0")
        if not _is_num(self.duration_sec) or float(self.duration_sec) <= 0:
            errors.append("timeline.duration_sec must be > 0")

        resolution = self.resolution
        if not (
            isinstance(resolution, tuple)
            and len(resolution) == 2
            and all(
                _is_int(value) and value > 0 and value % 2 == 0 for value in resolution
            )
        ):
            errors.append("timeline.resolution must be two positive even integers")

        match = (
            _ASPECT.match(self.aspect_ratio)
            if isinstance(self.aspect_ratio, str)
            else None
        )
        if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
            errors.append(
                "timeline.aspect_ratio must look like W:H with positive parts"
            )

        if not self.sources:
            errors.append("sources must not be empty")
        for key, ref in self.sources.items():
            if not isinstance(key, str) or not _SOURCE_KEY.match(key):
                errors.append(f"sources.{key}: invalid source_id")
            if not isinstance(ref, SourceRef):
                errors.append(f"sources.{key}: must be a SourceRef")
                continue
            if not isinstance(ref.path, str) or not ref.path:
                errors.append(f"sources.{key}.path must be non-empty")
            if not isinstance(ref.sha256, str) or not _SHA256.match(ref.sha256):
                errors.append(
                    f"sources.{key}.sha256 must be 64 lowercase hex characters"
                )
            if ref.duration is not None and (
                not _is_num(ref.duration) or float(ref.duration) <= 0
            ):
                errors.append(f"sources.{key}.duration must be > 0")
            if not isinstance(ref.camera, CameraSpec):
                errors.append(f"sources.{key}.camera must be a CameraSpec")
            else:
                if ref.camera.angle_deg is not None and not _is_num(
                    ref.camera.angle_deg
                ):
                    errors.append(f"sources.{key}.camera.angle_deg must be finite")
                if ref.camera.focal_length_mm is not None and (
                    not _is_num(ref.camera.focal_length_mm)
                    or float(ref.camera.focal_length_mm) <= 0
                ):
                    errors.append(f"sources.{key}.camera.focal_length_mm must be > 0")

        if not self.video_tracks:
            errors.append("tracks.video must not be empty")

        seen_clip_ids: Dict[str, str] = {}
        for kind, tracks in (
            ("video", self.video_tracks),
            ("audio", self.audio_tracks),
        ):
            seen_track_ids: set = set()
            for track_index, track in enumerate(tracks):
                base = f"tracks.{kind}[{track_index}]"
                if not isinstance(track, Track):
                    errors.append(f"{base}: must be a Track")
                    continue
                self._validate_track_header(track, base, kind, seen_track_ids, errors)
                for clip_index, clip in enumerate(track.clips):
                    clip_base = f"{base}.clips[{clip_index}]"
                    self._validate_clip(clip, clip_base, seen_clip_ids, errors)
                self._validate_track_overlap(track, base, errors)

        self._validate_coverage(errors)
        self._validate_dialogue_offsets(errors)
        self._validate_ducking(errors)
        return errors

    @staticmethod
    def _validate_track_header(
        track: Track, base: str, kind: str, seen_track_ids: set, errors: List[str]
    ) -> None:
        if not _is_int(track.track_id) or track.track_id < 0:
            errors.append(f"{base}.track_id must be an int >= 0")
        elif track.track_id in seen_track_ids:
            errors.append(f"{base}.track_id {track.track_id} is duplicated in {kind}")
        else:
            seen_track_ids.add(track.track_id)
        if not isinstance(track.role, str) or not track.role:
            errors.append(f"{base}.role must be non-empty")
        if not track.clips:
            errors.append(f"{base}.clips must not be empty")

    def _validate_clip(
        self, clip: Any, base: str, seen_clip_ids: Dict[str, str], errors: List[str]
    ) -> None:
        if not isinstance(clip, ClipSegment):
            errors.append(f"{base}: must be a ClipSegment")
            return

        if not isinstance(clip.clip_id, str) or not clip.clip_id:
            errors.append(f"{base}.clip_id must be non-empty")
        elif clip.clip_id in seen_clip_ids:
            errors.append(
                f"{base}.clip_id {clip.clip_id} is duplicated "
                f"(first at {seen_clip_ids[clip.clip_id]})"
            )
        else:
            seen_clip_ids[clip.clip_id] = base

        if not isinstance(clip.source_id, str) or not clip.source_id:
            errors.append(f"{base}.source_id must be non-empty")
        elif clip.source_id not in self.sources:
            errors.append(f"{base}.source_id {clip.source_id} is not in sources")

        if not isinstance(clip.role, str) or not clip.role:
            errors.append(f"{base}.role must be non-empty")

        if not (_is_num(clip.src_in) and _is_num(clip.src_out)):
            errors.append(f"{base}: src_in/src_out must be finite numbers")
        elif float(clip.src_out) <= float(clip.src_in):
            errors.append(f"{base}.src_out must be > src_in")

        if not (_is_num(clip.dst_in) and _is_num(clip.dst_out)):
            errors.append(f"{base}: dst_in/dst_out must be finite numbers")
        else:
            if float(clip.dst_out) <= float(clip.dst_in):
                errors.append(f"{base}.dst_out must be > dst_in")
            if float(clip.dst_in) < 0:
                errors.append(f"{base}.dst_in must be >= 0")
            if (
                _is_num(self.duration_sec)
                and float(clip.dst_out) > float(self.duration_sec) + 1e-6
            ):
                errors.append(
                    f"{base}.dst_out {clip.dst_out} exceeds duration_sec {self.duration_sec}"
                )
            if (
                _is_num(clip.src_out)
                and _is_num(clip.src_in)
                and float(clip.src_out) > float(clip.src_in)
                and _is_num(self.fps)
                and float(self.fps) > 0
            ):
                source_duration = float(clip.src_out) - float(clip.src_in)
                timeline_duration = float(clip.dst_out) - float(clip.dst_in)
                if (
                    abs(timeline_duration - source_duration)
                    > 1.0 / float(self.fps) + 1e-6
                ):
                    errors.append(
                        f"{base}: dst duration {timeline_duration:.4f} differs from "
                        f"src duration {source_duration:.4f} by more than one frame"
                    )

        if not _is_int(clip.layer) or clip.layer < 0:
            errors.append(f"{base}.layer must be an int >= 0")
        elif clip.role == "broll_overlay" and clip.layer < 1:
            errors.append(f"{base}: role broll_overlay requires layer >= 1")

        if not _is_num(clip.gain_db):
            errors.append(f"{base}.gain_db must be finite")
        elif not (-60.0 <= float(clip.gain_db) <= 24.0):
            errors.append(f"{base}.gain_db must be in [-60, 24]")

        for name, transition in (
            ("transition_in", clip.transition_in),
            ("transition_out", clip.transition_out),
        ):
            if transition is None:
                continue
            if not isinstance(transition, Transition):
                errors.append(f"{base}.{name}: must be a Transition")
                continue
            if not isinstance(transition.type, str) or not transition.type:
                errors.append(f"{base}.{name}.type must be non-empty")
            if (
                not _is_num(transition.duration_sec)
                or float(transition.duration_sec) < 0
            ):
                errors.append(f"{base}.{name}.duration_sec must be >= 0")
                continue
            clip_duration = min(
                abs(float(clip.dst_out) - float(clip.dst_in))
                if _is_num(clip.dst_out) and _is_num(clip.dst_in)
                else 0.0,
                1.5,
            )
            if float(transition.duration_sec) > clip_duration + 1e-6:
                errors.append(
                    f"{base}.{name}.duration_sec {transition.duration_sec} "
                    f"exceeds {clip_duration:.3f}"
                )

    @staticmethod
    def _validate_track_overlap(track: Track, base: str, errors: List[str]) -> None:
        clips = [clip for clip in track.clips if isinstance(clip, ClipSegment)]
        for layer in sorted({clip.layer for clip in clips if _is_int(clip.layer)}):
            ordered = sorted(
                (clip for clip in clips if clip.layer == layer),
                key=lambda clip: float(clip.dst_in or 0.0),
            )
            for previous, current in zip(ordered, ordered[1:]):
                if float(current.dst_in) < float(previous.dst_out) - 1e-6:
                    errors.append(
                        f"{base}: layer {layer} clips overlap at "
                        f"{float(current.dst_in):.3f}"
                    )

    def _validate_coverage(self, errors: List[str]) -> None:
        clips = [
            clip
            for track in self.video_tracks
            if isinstance(track, Track)
            for clip in track.clips
            if isinstance(clip, ClipSegment)
            and _is_num(clip.dst_in)
            and _is_num(clip.dst_out)
        ]
        if not clips:
            return

        layer0 = sorted(
            (clip for clip in clips if clip.layer == 0),
            key=lambda clip: float(clip.dst_in),
        )
        if layer0:
            if abs(float(layer0[0].dst_in)) > 1e-6:
                errors.append("tracks.video: layer-0 clips must start at 0")
            cursor = float(layer0[0].dst_out)
            for clip in layer0[1:]:
                start = float(clip.dst_in)
                if start > cursor + 1e-6:
                    errors.append(
                        f"tracks.video: layer-0 gap {cursor:.3f}..{start:.3f}"
                    )
                cursor = max(cursor, float(clip.dst_out))

        intervals = sorted(
            ((float(clip.dst_in), float(clip.dst_out)) for clip in clips),
            key=lambda item: item[0],
        )
        content_end = max(end for _, end in intervals)
        cursor = 0.0
        for start, end in intervals:
            if start > cursor + 1e-6:
                errors.append(f"tracks.video: gap {cursor:.3f}..{start:.3f}")
                break
            cursor = max(cursor, end)
        if cursor < content_end - 1e-6:
            errors.append(
                f"tracks.video: coverage stops at {cursor:.3f} of {content_end:.3f}"
            )

    def _validate_dialogue_offsets(self, errors: List[str]) -> None:
        boundaries = sorted(
            {
                float(value)
                for track in self.video_tracks
                if isinstance(track, Track)
                for clip in track.clips
                if isinstance(clip, ClipSegment) and clip.layer == 0
                for value in (clip.dst_in, clip.dst_out)
                if _is_num(value)
            }
        )
        if not boundaries:
            return
        for track_index, track in enumerate(self.audio_tracks):
            if not isinstance(track, Track) or track.role != "dialogue":
                continue
            for clip_index, clip in enumerate(track.clips):
                if not isinstance(clip, ClipSegment):
                    continue
                for value in (clip.dst_in, clip.dst_out):
                    if not _is_num(value):
                        continue
                    boundary = float(value)
                    if boundary <= 1e-6 or boundary >= float(self.duration_sec) - 1e-6:
                        continue
                    distance = min(abs(boundary - edge) for edge in boundaries)
                    if distance > 1.5 + 1e-6:
                        errors.append(
                            f"tracks.audio[{track_index}].clips[{clip_index}]: "
                            f"dialogue boundary {boundary:.3f} is {distance:.3f}s "
                            f"from the nearest video cut (max 1.5s)"
                        )

    def _validate_ducking(self, errors: List[str]) -> None:
        spec = self.ducking
        if spec is None:
            return
        if not isinstance(spec, DuckingSpec):
            errors.append("ducking must be a DuckingSpec")
            return

        for name in ("sidechain_role", "target_role"):
            value = getattr(spec, name)
            if not isinstance(value, str) or not value:
                errors.append(f"ducking.{name} must be non-empty")
        if (
            isinstance(spec.sidechain_role, str)
            and isinstance(spec.target_role, str)
            and spec.sidechain_role
            and spec.target_role
            and spec.sidechain_role == spec.target_role
        ):
            errors.append("ducking.sidechain_role and ducking.target_role must differ")

        for name, low, high in (
            ("threshold", 0.000976563, 1.0),
            ("ratio", 1.0, 20.0),
            ("attack_ms", 0.01, 2000.0),
            ("release_ms", 0.01, 9000.0),
            ("makeup_db", 0.0, 36.0),
        ):
            value = getattr(spec, name)
            if not _is_num(value):
                errors.append(f"ducking.{name} must be finite")
            elif not (low <= float(value) <= high):
                errors.append(f"ducking.{name} must be in [{low}, {high}]")

        roles = {track.role for track in self.audio_tracks if isinstance(track, Track)}
        for name in ("sidechain_role", "target_role"):
            value = getattr(spec, name)
            if isinstance(value, str) and value and value not in roles:
                errors.append(f"ducking.{name} {value!r} is not an audio track role")

    # -- serialization ----------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "contract_version": self.contract_version,
            "timeline": {
                "fps": self.fps,
                "duration_sec": self.duration_sec,
                "aspect_ratio": self.aspect_ratio,
                "resolution": [int(self.resolution[0]), int(self.resolution[1])],
            },
            "sources": {key: _source_to_dict(ref) for key, ref in self.sources.items()},
            "tracks": {
                "video": [
                    _track_to_dict(track, video=True) for track in self.video_tracks
                ],
                "audio": [
                    _track_to_dict(track, video=False) for track in self.audio_tracks
                ],
            },
        }
        if self.ducking is not None:
            data["ducking"] = self.ducking.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EDLPlan":
        errors: List[str] = []
        if not isinstance(data, dict):
            raise EDLValidationError(["plan: must be an object"])
        _reject_unknown(data, _TOP_KEYS, "", errors)

        timeline = data.get("timeline")
        if not isinstance(timeline, dict):
            errors.append("timeline: must be an object")
            timeline = {}
        _reject_unknown(timeline, _TIMELINE_KEYS, "timeline", errors)

        sources: Dict[str, SourceRef] = {}
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, dict):
            errors.append("sources: must be an object")
            raw_sources = {}
        for key, raw in raw_sources.items():
            path = f"sources.{key}"
            if not isinstance(raw, dict):
                errors.append(f"{path}: must be an object")
                continue
            _reject_unknown(raw, _SOURCE_KEYS, path, errors)
            camera_raw = raw.get("camera")
            if isinstance(camera_raw, dict):
                _reject_unknown(camera_raw, _CAMERA_KEYS, f"{path}.camera", errors)
            try:
                camera = CameraSpec.from_dict(camera_raw)
            except (TypeError, ValueError) as exc:
                errors.append(f"{path}.camera: {exc}")
                camera = CameraSpec()
            sources[str(key)] = SourceRef(
                path=raw.get("path", ""),
                sha256=raw.get("sha256", ""),
                duration=raw.get("duration"),
                camera=camera,
            )

        raw_tracks = data.get("tracks")
        if not isinstance(raw_tracks, dict):
            errors.append("tracks: must be an object")
            raw_tracks = {}
        _reject_unknown(raw_tracks, {"video", "audio"}, "tracks", errors)

        video_tracks = _parse_tracks(
            raw_tracks.get("video"), "video", errors, clip_factory=_clip_from_dict
        )
        audio_tracks = _parse_tracks(
            raw_tracks.get("audio"), "audio", errors, clip_factory=_audio_track_clips
        )

        ducking: Optional[DuckingSpec] = None
        raw_ducking = data.get("ducking")
        if raw_ducking is not None:
            if not isinstance(raw_ducking, dict):
                errors.append("ducking: must be an object")
            else:
                _reject_unknown(raw_ducking, _DUCKING_KEYS, "ducking", errors)
                try:
                    ducking = DuckingSpec.from_dict(raw_ducking)
                except (TypeError, ValueError) as exc:
                    errors.append(f"ducking: {exc}")

        plan = cls(
            contract_version=data.get("contract_version", EDL_CONTRACT_VERSION),
            fps=timeline.get("fps"),
            duration_sec=timeline.get("duration_sec"),
            aspect_ratio=timeline.get("aspect_ratio"),
            resolution=timeline.get("resolution"),
            sources=sources,
            video_tracks=video_tracks,
            audio_tracks=audio_tracks,
            ducking=ducking,
        )
        errors.extend(plan.validate())
        if errors:
            raise EDLValidationError(errors)
        return plan


def _source_to_dict(ref: SourceRef) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "path": ref.path,
        "sha256": ref.sha256,
        "camera": ref.camera.to_dict(),
    }
    if ref.duration is not None:
        data["duration"] = ref.duration
    return data


def _transition_to_dict(transition: Transition) -> Dict[str, Any]:
    return {"type": transition.type, "duration_sec": transition.duration_sec}


def _clip_to_dict(clip: ClipSegment) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "clip_id": clip.clip_id,
        "source_id": clip.source_id,
        "src_in": clip.src_in,
        "src_out": clip.src_out,
        "dst_in": clip.dst_in,
        "dst_out": clip.dst_out,
        "layer": clip.layer,
        "role": clip.role,
    }
    if clip.transition_in is not None:
        data["transition_in"] = _transition_to_dict(clip.transition_in)
    if clip.transition_out is not None:
        data["transition_out"] = _transition_to_dict(clip.transition_out)
    if clip.gain_db != 0.0:
        data["gain_db"] = clip.gain_db
    return data


def _track_to_dict(track: Track, *, video: bool) -> Dict[str, Any]:
    data: Dict[str, Any] = {"track_id": track.track_id, "role": track.role}
    if not video and len(track.clips) == 1 and track.clips[0].gain_db == 0.0:
        clip = track.clips[0]
        data.update(
            {
                "source_id": clip.source_id,
                "src_in": clip.src_in,
                "src_out": clip.src_out,
                "dst_in": clip.dst_in,
                "dst_out": clip.dst_out,
            }
        )
        return data
    data["clips"] = [_clip_to_dict(clip) for clip in track.clips]
    return data


def _parse_tracks(
    raw: Any, kind: str, errors: List[str], *, clip_factory
) -> Tuple[Track, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(f"tracks.{kind}: must be a list")
        return ()
    tracks: List[Track] = []
    for index, raw_track in enumerate(raw):
        base = f"tracks.{kind}[{index}]"
        if not isinstance(raw_track, dict):
            errors.append(f"{base}: must be an object")
            continue
        _reject_unknown(
            raw_track, _TRACK_KEYS if kind == "video" else _AUDIO_KEYS, base, errors
        )
        track_id = raw_track.get("track_id")
        role = raw_track.get("role", "")
        if kind == "video":
            clips = _clip_list(raw_track.get("clips"), base, errors, track_role=role)
        else:
            clips = clip_factory(raw_track, base, errors)
        tracks.append(Track(track_id=track_id, clips=tuple(clips), role=role))
    return tuple(tracks)


def _clip_list(
    raw: Any, base: str, errors: List[str], *, track_role: str
) -> List[ClipSegment]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"{base}.clips: must be a list")
        return []
    clips: List[ClipSegment] = []
    for index, raw_clip in enumerate(raw):
        path = f"{base}.clips[{index}]"
        if not isinstance(raw_clip, dict):
            errors.append(f"{path}: must be an object")
            continue
        _reject_unknown(raw_clip, _CLIP_KEYS, path, errors)
        clips.append(_clip_from_dict(raw_clip, path, errors, default_role=track_role))
    return clips


def _audio_track_clips(
    raw_track: Dict[str, Any], base: str, errors: List[str]
) -> List[ClipSegment]:
    direct = {"source_id", "src_in", "src_out", "dst_in", "dst_out"}
    present = direct & set(raw_track)
    if present and "clips" in raw_track:
        errors.append(f"{base}: cannot mix direct audio fields with clips")
        return []
    if "clips" in raw_track:
        return _clip_list(
            raw_track.get("clips"),
            base,
            errors,
            track_role=str(raw_track.get("role") or "dialogue"),
        )
    if not present:
        return []
    return [
        ClipSegment(
            clip_id=f"a{raw_track.get('track_id')}_0",
            source_id=raw_track.get("source_id"),
            src_in=raw_track.get("src_in"),
            src_out=raw_track.get("src_out"),
            dst_in=raw_track.get("dst_in"),
            dst_out=raw_track.get("dst_out"),
            layer=0,
            role=str(raw_track.get("role") or "dialogue"),
        )
    ]


def _clip_from_dict(
    raw: Dict[str, Any],
    path: str,
    errors: List[str],
    *,
    default_role: str = "main_talking_head",
) -> ClipSegment:
    clip_id = raw.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        clip_id = (
            path.replace("tracks.", "")
            .replace(".", "_")
            .replace("[", "_")
            .replace("]", "")
        )
    return ClipSegment(
        clip_id=clip_id,
        source_id=raw.get("source_id"),
        src_in=raw.get("src_in"),
        src_out=raw.get("src_out"),
        dst_in=raw.get("dst_in"),
        dst_out=raw.get("dst_out"),
        layer=raw.get("layer", 0),
        role=raw.get("role", default_role) or default_role,
        transition_in=_transition_from_dict(
            raw.get("transition_in"), f"{path}.transition_in", errors
        ),
        transition_out=_transition_from_dict(
            raw.get("transition_out"), f"{path}.transition_out", errors
        ),
        gain_db=raw.get("gain_db", 0.0),
    )


def _transition_from_dict(
    raw: Any, path: str, errors: List[str]
) -> Optional[Transition]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append(f"{path}: must be an object")
        return Transition("", 0.0)
    _reject_unknown(raw, _TRANSITION_KEYS, path, errors)
    return Transition(
        type=raw.get("type", ""),
        duration_sec=raw.get("duration_sec", 0.0),
    )
