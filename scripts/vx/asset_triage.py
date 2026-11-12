"""vx.asset_triage — decide which media earns a place on the timeline.

The triage runs before any planning: it classifies a heterogeneous pool
(video, stills, music), measures what it can, and rejects an asset only when
the measurement is unambiguous. Every rejection carries a formal reason and
the numbers behind it, so an omission is as legible as a selection.

Two rules dominate this module:

* **declared omission** — an asset that never reaches the timeline appears in
  ``rejected`` with a reason from ``REJECTIONS`` and a non-empty ``details``;
  a metric that could not be measured is named in ``not_evaluated`` instead of
  being invented;
* **inaction under uncertainty** — a heuristic whose confidence sits inside
  ``[triage_confidence_low, triage_confidence_high]`` never rejects. It selects
  the asset and flags it ``uncertain``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import contract, probe

KINDS = frozenset({"video", "image", "audio"})

REJECTIONS = frozenset(
    {
        "UNREADABLE",
        "TOO_SHORT",
        "RESOLUTION_TOO_LOW",
        "DUPLICATE_ASSET",
        "AUDIO_TOO_NOISY",
        "VIDEO_TOO_BLURRY",
        "VIDEO_TOO_DARK",
        "MUSIC_INCOHERENT",
        "NO_SEMANTIC_ANCHOR",
        "REDUNDANT_BROLL",
        "REDUNDANT_MUSIC",
    }
)

ROLES = frozenset({"a_roll", "b_roll", "music", "dialogue"})

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts"})
IMAGE_SUFFIXES = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".avif",
    }
)
AUDIO_SUFFIXES = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".opus"}
)

_MANAGED_KEYS = frozenset({"path", "role", "at_sec", "treatment", "ducking_db"})
_MANIFEST_TREATMENT = "_manifest_treatment"
_MANIFEST_DUCKING = "_manifest_ducking_db"

_TRIAGE_KEYS = (
    "triage_max_upscale",
    "triage_adapt_max_upscale",
    "triage_adapt_target_upscale",
    "triage_max_broll",
    "triage_max_music",
    "triage_min_duration_sec",
    "triage_broll_sec",
    "triage_image_duration_sec",
    "triage_music_gain_db",
    "triage_music_ratio_low",
    "triage_sharpness_low",
    "triage_confidence_low",
    "triage_confidence_high",
    "triage_phash_max_distance",
    "triage_phash_duration_tolerance_sec",
)


@dataclass(frozen=True)
class AssetInfo:
    asset_id: str
    kind: str
    path: str
    width: int = 0
    height: int = 0
    duration_sec: float = 0.0
    has_audio: bool = False
    sha256: str = ""
    size: int = 0
    role: Optional[str] = None
    at_sec: Optional[float] = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    #: Position in discovery: the manifest order when there is a manifest, the
    #: file name otherwise. ``-1`` means "not discovered" and keeps the
    #: alphabetical order a hand-built pool has always had.
    order_index: int = -1


@dataclass(frozen=True)
class TriageDecision:
    asset_id: str
    selected: bool
    role: str
    target_duration_sec: float
    treatment: str = "none"
    ducking_db: float = 0.0
    rejection_reason: Optional[str] = None
    details: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True)
class TriageResult:
    selected: Tuple[TriageDecision, ...]
    rejected: Tuple[TriageDecision, ...]
    not_evaluated: Tuple[str, ...]
    thresholds: Mapping[str, Any]
    role_source: str
    kinds: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    #: Declared adaptation of the canvas (``None`` when the material fit as asked).
    adapted: Optional[Mapping[str, Any]] = None

    def _decision_dict(self, decision: TriageDecision) -> Dict[str, Any]:
        return {
            "asset_id": decision.asset_id,
            "kind": self.kinds.get(decision.asset_id, ""),
            "selected": decision.selected,
            "role": decision.role,
            "target_duration_sec": decision.target_duration_sec,
            "treatment": decision.treatment,
            "ducking_db": decision.ducking_db,
            "rejection_reason": decision.rejection_reason,
            "details": dict(decision.details),
            "confidence": decision.confidence,
        }

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "selected": [self._decision_dict(item) for item in self.selected],
            "rejected": [self._decision_dict(item) for item in self.rejected],
            "not_evaluated": list(self.not_evaluated),
            "thresholds": dict(self.thresholds),
            "role_source": self.role_source,
        }
        if self.adapted:
            data["adapted"] = dict(self.adapted)
        return data

    def payload_block(self) -> Dict[str, Any]:
        """External contract: the key names a scene payload is allowed to carry."""
        selected: List[Dict[str, Any]] = []
        for item in self.selected:
            entry: Dict[str, Any] = {
                "asset_id": item.asset_id,
                "type": self.kinds.get(item.asset_id, ""),
                "role": item.role,
                "target_duration_sec": item.target_duration_sec,
                "treatment": item.treatment,
            }
            if item.ducking_db:
                entry["ducking_db"] = item.ducking_db
            selected.append(entry)
        rejected = [
            {
                "asset_id": item.asset_id,
                "type": self.kinds.get(item.asset_id, ""),
                "rejection_reason": item.rejection_reason,
                "details": dict(item.details),
            }
            for item in self.rejected
        ]
        block: Dict[str, Any] = {
            "selected_assets": selected,
            "rejected_assets": rejected,
            "not_evaluated": list(self.not_evaluated),
            "thresholds": dict(self.thresholds),
            "role_source": self.role_source,
        }
        # Only when something was adapted: an unchanged canvas keeps the block
        # shape every older consumer already validates.
        if self.adapted:
            block["adapted"] = dict(self.adapted)
        return block


# --------------------------------------------------------------------------- #
# thresholds and small helpers
# --------------------------------------------------------------------------- #


def _effective_thresholds() -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for key in _TRIAGE_KEYS:
        values[key] = contract.THRESHOLDS[key]
    values["noise_snr_low"] = contract.THRESHOLDS["noise_snr_low"]
    values["luma_under"] = contract.THRESHOLDS["luma_under"]
    return values


def _confidence(value: float, threshold: float) -> float:
    """Relative distance from the threshold, floored at 1% of it.

    The floor keeps a threshold of 0 from dividing by zero; using the
    threshold itself (not a fixed 1.0) keeps a 0..1 ratio check meaningful.
    """
    floor = max(0.01, abs(float(threshold)))
    return min(1.0, abs(float(value) - float(threshold)) / floor)


def _target_size(value: Any) -> Tuple[int, int]:
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+)\s*[xX:]\s*(\d+)\s*", value)
        if not match:
            raise ValueError(f"target_resolution non riconosciuta: {value!r}")
        width, height = int(match.group(1)), int(match.group(2))
    elif isinstance(value, Mapping):
        width = int(value.get("width", value.get("w", 0)) or 0)
        height = int(value.get("height", value.get("h", 0)) or 0)
    else:
        try:
            width, height = (int(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"target_resolution non riconosciuta: {value!r}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"target_resolution deve essere positiva: {value!r}")
    return width, height


def _sha256(path: Path, size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind_from_suffix(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return None


# --------------------------------------------------------------------------- #
# perceptual fingerprint (A9)
# --------------------------------------------------------------------------- #

# Relative positions of the sampled frames. Relative, not absolute: an encode
# that starts a frame earlier still lands on the same content.
PHASH_POSITIONS: Tuple[float, ...] = (0.15, 0.50, 0.85)
PHASH_SIDE = 8
PHASH_BITS_PER_FRAME = PHASH_SIDE * PHASH_SIDE


class FingerprintError(RuntimeError):
    """The clip could not be decoded far enough to be fingerprinted."""


def perceptual_fingerprint(
    path: str | Path, *, positions: Sequence[float] = PHASH_POSITIONS
) -> str:
    """Sample frames at fixed relative positions and return their dHashes.

    Each frame is reduced to a 9x8 thumbnail and turned into ``PHASH_SIDE**2``
    bits, one per horizontal neighbour pair (``left > right``), so a change in
    brightness or codec noise that preserves structure does not move the hash.
    The blocks of all samples are concatenated as hex, one fixed-width block per
    sample, which makes the normalised Hamming distance between two fingerprints
    a straight bit comparison.

    Raising ``FingerprintError`` is deliberate: the caller declares the absent
    measurement instead of reading a decode failure as a difference.
    """
    try:
        import cv2
    except Exception as exc:  # noqa: BLE001 - absent dependency is declared upstream
        raise FingerprintError(f"opencv unavailable: {exc}") from exc

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise FingerprintError("cannot open the file")
    try:
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        duration = (frames / fps) if (fps > 0.0 and frames > 0.0) else 0.0
        if duration <= 0.0:
            raise FingerprintError("unknown duration")
        blocks: List[str] = []
        for ratio in positions:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(ratio) * duration * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise FingerprintError(f"no decodable frame at {float(ratio):.2f}")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            thumb = cv2.resize(gray, (PHASH_SIDE + 1, PHASH_SIDE))
            bits = thumb[:, 1:] > thumb[:, :-1]
            value = int("".join("1" if bit else "0" for bit in bits.flatten()), 2)
            blocks.append(f"{value:0{PHASH_BITS_PER_FRAME // 4}x}")
    finally:
        capture.release()
    return "".join(blocks)


def fingerprint_distance(left: str, right: str) -> Optional[float]:
    """Normalised Hamming distance in 0..1, or ``None`` when incomparable."""
    if not left or not right or len(left) != len(right):
        return None
    try:
        differing = sum(
            bin(int(a, 16) ^ int(b, 16)).count("1") for a, b in zip(left, right)
        )
    except ValueError:
        return None
    return differing / float(len(left) * 4)


def perceptual_asset_fingerprint(asset: AssetInfo) -> str:
    return perceptual_fingerprint(asset.path)


def _perceptual_duplicates(
    assets: Sequence[AssetInfo],
    *,
    fingerprint: Callable[[AssetInfo], str],
    thresholds: Mapping[str, Any],
    not_evaluated: set[str],
) -> Dict[str, Dict[str, Any]]:
    """Map each near-duplicate asset to the survivor it duplicates.

    ``assets`` arrives in the deterministic triage order, so the survivor of a
    cluster is its first member in that order and the verdict cannot depend on
    the caller's ordering. A fingerprint is computed only when an earlier video
    with the same resolution and a duration inside the tolerance exists, which
    bounds the decode cost to the pairs that can actually match. A decode failure
    is declared in ``not_evaluated`` and never removes the asset from the pool.
    """
    max_distance = float(thresholds["triage_phash_max_distance"])
    tolerance = float(thresholds["triage_phash_duration_tolerance_sec"])
    matches: Dict[str, Dict[str, Any]] = {}
    digests: Dict[str, str] = {}
    failed: set[str] = set()

    bands: Dict[Tuple[int, int], List[AssetInfo]] = {}
    for asset in assets:
        if asset.kind != "video" or asset.width <= 0 or asset.height <= 0:
            continue
        if asset.duration_sec <= 0.0:
            continue
        bands.setdefault((int(asset.width), int(asset.height)), []).append(asset)

    def _digest(asset: AssetInfo) -> Optional[str]:
        if asset.asset_id in digests:
            return digests[asset.asset_id]
        if asset.asset_id in failed:
            return None
        try:
            digests[asset.asset_id] = str(fingerprint(asset))
        except Exception as exc:  # noqa: BLE001 - absence is declared, not guessed
            failed.add(asset.asset_id)
            not_evaluated.add(f"visual_fingerprint: {type(exc).__name__}: {exc}")
            return None
        return digests[asset.asset_id]

    for band in bands.values():
        if len(band) < 2:
            continue
        survivors: List[AssetInfo] = []
        for asset in band:
            comparable = [
                other
                for other in survivors
                if abs(asset.duration_sec - other.duration_sec) <= tolerance
            ]
            if not comparable:
                survivors.append(asset)
                continue
            digest = _digest(asset)
            if digest is None:
                survivors.append(asset)
                continue
            survivor: Optional[AssetInfo] = None
            distance: Optional[float] = None
            for other in comparable:
                other_digest = _digest(other)
                if other_digest is None:
                    continue
                measured = fingerprint_distance(digest, other_digest)
                if measured is None or measured > max_distance:
                    continue
                survivor = other
                distance = measured
                break
            if survivor is None or distance is None:
                survivors.append(asset)
                continue
            matches[asset.asset_id] = {
                "duplicate_of": survivor.asset_id,
                "method": "phash",
                "distance": round(distance, 4),
                "similarity": round(1.0 - distance, 4),
                "max_distance": max_distance,
                "duration_sec": round(float(asset.duration_sec), 3),
                "duration_tolerance_sec": tolerance,
                "samples": len(PHASH_POSITIONS),
            }
    return matches


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


def _read_manifest(manifest: Any) -> Optional[Dict[str, Mapping[str, Any]]]:
    if manifest is None:
        return None
    if isinstance(manifest, Mapping):
        data: Any = manifest
    else:
        # ``utf-8-sig``: PowerShell's ``Set-Content -Encoding utf8`` writes a BOM,
        # and a manifest is a file a human hands us, not a place to be strict.
        data = json.loads(Path(manifest).read_text(encoding="utf-8-sig"))
    if not isinstance(data, Mapping) or not isinstance(data.get("assets"), list):
        raise ValueError("manifest: atteso un oggetto con la lista 'assets'")
    entries: Dict[str, Mapping[str, Any]] = {}
    for index, entry in enumerate(data["assets"]):
        if not isinstance(entry, Mapping):
            raise ValueError(f"manifest.assets[{index}]: atteso un oggetto")
        unknown = set(entry) - _MANAGED_KEYS
        if unknown:
            raise ValueError(
                f"manifest.assets[{index}]: chiavi sconosciute {sorted(unknown)}"
            )
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"manifest.assets[{index}].path: percorso mancante")
        role = entry.get("role")
        if role is not None and role not in ROLES:
            raise ValueError(
                f"manifest.assets[{index}].role: ruolo sconosciuto {role!r}"
            )
        if entry.get("at_sec") is not None:
            try:
                float(entry["at_sec"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"manifest.assets[{index}].at_sec: non numerico"
                ) from exc
        entries[Path(raw).as_posix().lower()] = entry
    return entries


def _manifest_entry(
    directory: Path, path: Path, entries: Mapping[str, Mapping[str, Any]]
) -> Optional[Mapping[str, Any]]:
    raw = None
    for key, entry in entries.items():
        candidate = Path(key)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(
                f"manifest: percorso non relativo o esterno: {entry['path']!r}"
            )
        if (directory / candidate).resolve() == path.resolve():
            raw = entry
            break
    return raw


def _discovery_key(
    path: Path,
    root: Path,
    entries: Optional[Mapping[str, Mapping[str, Any]]],
) -> Tuple[int, int, str]:
    """Manifest order first, then the file name.

    A manifest is a human decision about sequence, so it wins over the
    alphabetical listing; without one the order stays the file name. An asset
    the manifest does not mention comes after the declared ones, in name order,
    instead of disappearing from the pool.
    """
    if entries:
        key = path.relative_to(root).as_posix().lower()
        if key in entries:
            position = list(entries).index(key)
            return (0, position, path.name.lower())
    return (1, 0, path.name.lower())


def discover_assets(
    directory: str | Path,
    *,
    manifest: Any = None,
    measure: Optional[Callable[[AssetInfo], Mapping[str, Any]]] = None,
    skipped: Optional[List[Dict[str, Any]]] = None,
) -> List[AssetInfo]:
    """Scan the TOP LEVEL of ``directory`` and probe every recognised media file.

    Non-recursive on purpose: a nested tree hides its own duplicates and makes
    the report impossible to map back to a folder the user can point at.

    ``skipped`` collects the files that are **not** media by extension. Prima
    sparivano con un ``continue`` nudo: una cartella con dentro il progetto
    Premiere e i suoi file di appoggio sembrava identica a una cartella pulita,
    e chi guardava il report non poteva sapere che qualcosa era stato ignorato.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"directory inesistente: {root}")
    entries = _read_manifest(manifest)
    if entries is not None:
        for key in entries:
            candidate = Path(key)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"manifest: percorso non relativo o esterno: {key!r}")

    files = sorted(
        (
            item
            for item in root.iterdir()
            if item.is_file() and not item.name.startswith(".")
        ),
        key=lambda item: _discovery_key(item, root, entries),
    )

    assets: List[AssetInfo] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        kind = _kind_from_suffix(path)
        if kind is None:
            if skipped is not None:
                skipped.append(
                    {
                        "asset_id": rel,
                        "reason": "unknown_extension",
                        "suffix": path.suffix.lower(),
                    }
                )
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        metrics: Dict[str, Any] = {}
        try:
            info = probe.probe_asset(path)
        except Exception as exc:  # noqa: BLE001 - an unreadable file is a decision
            info = None
            metrics["probe_error"] = f"{type(exc).__name__}: {exc}"
        if info is None:
            assets.append(
                AssetInfo(
                    asset_id=rel,
                    kind=kind,
                    path=str(path),
                    size=size,
                    metrics=metrics,
                    order_index=len(assets),
                )
            )
            continue
        asset = AssetInfo(
            asset_id=rel,
            kind=info.kind,
            path=str(path),
            width=int(info.width or 0),
            height=int(info.height or 0),
            duration_sec=float(info.duration or 0.0),
            has_audio=bool(info.has_audio),
            size=size,
            metrics=metrics,
            order_index=len(assets),
        )
        if measure is not None:
            try:
                metrics.update(dict(measure(asset) or {}))
            except Exception as exc:  # noqa: BLE001 - measurement is best effort
                metrics["measure_error"] = f"{type(exc).__name__}: {exc}"
            asset = AssetInfo(
                asset_id=asset.asset_id,
                kind=asset.kind,
                path=asset.path,
                width=asset.width,
                height=asset.height,
                duration_sec=asset.duration_sec,
                has_audio=asset.has_audio,
                size=asset.size,
                metrics=metrics,
                order_index=asset.order_index,
            )
        assets.append(asset)

    by_size: Dict[int, List[AssetInfo]] = {}
    for asset in assets:
        if asset.size > 0:
            by_size.setdefault(asset.size, []).append(asset)
    fingerprints: Dict[str, str] = {}
    for group in by_size.values():
        if len(group) < 2:
            continue
        for asset in group:
            try:
                fingerprints[asset.asset_id] = _sha256(Path(asset.path))
            except OSError:
                continue

    resolved: List[AssetInfo] = []
    for asset in assets:
        entry = _manifest_entry(root, Path(asset.path), entries) if entries else None
        metrics = dict(asset.metrics)
        role = asset.role
        at_sec = asset.at_sec
        if entry is not None:
            role = entry.get("role", role)
            if entry.get("at_sec") is not None:
                at_sec = float(entry["at_sec"])
            if entry.get("treatment") is not None:
                metrics[_MANIFEST_TREATMENT] = entry["treatment"]
            if entry.get("ducking_db") is not None:
                metrics[_MANIFEST_DUCKING] = float(entry["ducking_db"])
        resolved.append(
            AssetInfo(
                asset_id=asset.asset_id,
                kind=asset.kind,
                path=asset.path,
                width=asset.width,
                height=asset.height,
                duration_sec=asset.duration_sec,
                has_audio=asset.has_audio,
                sha256=fingerprints.get(asset.asset_id, asset.sha256),
                size=asset.size,
                role=role,
                at_sec=at_sec,
                metrics=metrics,
                order_index=asset.order_index,
            )
        )
    return resolved


def measure_asset(asset: AssetInfo) -> Dict[str, Any]:
    """Measure what is cheap and available; never invent a missing number."""
    metrics: Dict[str, Any] = {}
    if asset.kind in ("video", "image"):
        try:
            from . import visual

            sharpness = visual.measure_sharpness(asset.path)
            method = str(sharpness.get("method") or "")
            measured = int(sharpness.get("samples") or 0) > 0 and not method.startswith(
                "unavailable"
            )
            if measured and sharpness.get("reliable", True):
                metrics["sharpness"] = float(sharpness["sharpness"])
                metrics["sharpness_mean_luma"] = float(sharpness.get("mean_luma") or 0.0)
            elif measured:
                # D-21: below the luma floor the Laplacian variance ranks a dark,
                # blurry clip above a bright, good one. The number is kept as a
                # description and the gate declares the check not evaluated.
                metrics["sharpness_descriptive"] = float(sharpness["sharpness"])
                metrics["sharpness_mean_luma"] = float(sharpness.get("mean_luma") or 0.0)
                metrics["sharpness_reliable"] = False
            else:
                metrics["measure_error"] = method or "sharpness unavailable"
        except Exception as exc:  # noqa: BLE001 - absent metric is declared downstream
            metrics["measure_error"] = f"{type(exc).__name__}: {exc}"
    if asset.kind == "video":
        try:
            from . import analyze

            exposure = analyze.measure_exposure(asset.path, max_seconds=2.0)
            if str(exposure.get("verdict") or "") != "unknown":
                metrics["mean_luma"] = float(exposure["mean_luma"])
                metrics["luma_verdict"] = str(exposure.get("verdict") or "")
            else:
                metrics["measure_error"] = str(
                    exposure.get("note") or "exposure unavailable"
                )
        except Exception as exc:  # noqa: BLE001
            metrics["measure_error"] = f"{type(exc).__name__}: {exc}"
    if asset.kind == "audio":
        metrics["duration_sec"] = float(asset.duration_sec)
        metrics["has_audio"] = bool(asset.has_audio)
    return metrics


# --------------------------------------------------------------------------- #
# triage
# --------------------------------------------------------------------------- #


def _assign_roles(
    assets: Sequence[AssetInfo], *, from_manifest: bool, montage: bool = False
) -> Dict[str, str]:
    """Assign one role per asset.

    Default: exactly one dialogue-carrying video is the a_roll, everything else
    falls to b_roll or music. ``montage`` lifts the uniqueness rule only: every
    video eligible for the a_roll (it carries audio) keeps that role, in the
    order the triage iterates the pool. The quality gates that reject an asset
    run later and are untouched, so a montage candidate is still refused with
    its own rejection code when it is too short, too small or too noisy.
    """
    roles: Dict[str, str] = {}
    if from_manifest:
        for asset in assets:
            if asset.role is not None:
                roles[asset.asset_id] = asset.role
    claimed = set(roles.values()) & {"a_roll"}
    take_all = bool(montage)
    for asset in assets:
        if asset.asset_id in roles:
            continue
        if asset.kind == "audio":
            roles[asset.asset_id] = "music"
        elif (
            asset.kind == "video"
            and asset.has_audio
            and (take_all or "a_roll" not in claimed)
        ):
            roles[asset.asset_id] = "a_roll"
            claimed.add("a_roll")
        else:
            roles[asset.asset_id] = "b_roll"
    return roles


def _effective_duration(asset: AssetInfo, thresholds: Mapping[str, Any]) -> float:
    if asset.kind == "image":
        return float(thresholds["triage_image_duration_sec"])
    return float(asset.duration_sec)


def _target_duration(
    asset: AssetInfo, role: str, thresholds: Mapping[str, Any]
) -> float:
    if role == "b_roll" and asset.kind == "image":
        return float(thresholds["triage_image_duration_sec"])
    if role == "b_roll":
        return min(float(thresholds["triage_broll_sec"]), float(asset.duration_sec))
    return float(asset.duration_sec)


def _resolution_score(asset: AssetInfo) -> int:
    return int(asset.width) * int(asset.height)


def _has_anchor(asset: AssetInfo) -> bool:
    return asset.at_sec is not None


def _metric_specs(
    asset: AssetInfo, thresholds: Mapping[str, Any]
) -> List[Tuple[str, str, float, str]]:
    specs: List[Tuple[str, str, float, str]] = []
    if asset.kind in ("video", "image"):
        specs.append(
            (
                "video_sharpness",
                "sharpness",
                float(thresholds["triage_sharpness_low"]),
                "VIDEO_TOO_BLURRY",
            )
        )
    if asset.kind == "video":
        specs.append(
            (
                "video_exposure",
                "mean_luma",
                float(thresholds["luma_under"]),
                "VIDEO_TOO_DARK",
            )
        )
    if asset.kind == "audio" or asset.has_audio:
        specs.append(
            (
                "audio_noise",
                "snr_db",
                float(thresholds["noise_snr_low"]),
                "AUDIO_TOO_NOISY",
            )
        )
    if asset.kind == "audio":
        specs.append(
            (
                "music_incoherent",
                "music_ratio",
                float(thresholds["triage_music_ratio_low"]),
                "MUSIC_INCOHERENT",
            )
        )
    return specs


def _decision(
    asset: AssetInfo,
    *,
    role: str,
    selected: bool,
    target_duration_sec: float = 0.0,
    treatment: str = "none",
    ducking_db: float = 0.0,
    reason: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
    confidence: float = 1.0,
) -> TriageDecision:
    return TriageDecision(
        asset_id=asset.asset_id,
        selected=selected,
        role=role,
        target_duration_sec=float(target_duration_sec),
        treatment=treatment,
        ducking_db=float(ducking_db),
        rejection_reason=reason,
        details=dict(details or {}),
        confidence=float(confidence),
    )


# Relative canvas steps tried when the requested resolution cannot host the
# material: the vertical standards 1080x1920 -> 720x1280 -> 540x960 are the
# 1 / 2/3 / 1/2 rungs of this ladder, and the same fractions hold for 16:9.
RESOLUTION_LADDER: Tuple[float, ...] = (1.0, 2.0 / 3.0, 0.5)


def _even(value: float) -> int:
    """Nearest positive even integer: the EDL rejects an odd canvas."""
    return max(2, int(round(value / 2.0)) * 2)


def resolution_ladder(target_w: int, target_h: int) -> List[Tuple[int, int]]:
    """The canvas standards from the requested size downwards, highest first."""
    out: List[Tuple[int, int]] = [(int(target_w), int(target_h))]
    for scale in RESOLUTION_LADDER[1:]:
        pair = (_even(target_w * scale), _even(target_h * scale))
        if pair not in out and pair[0] < int(target_w) and pair[1] < int(target_h):
            out.append(pair)
    return out


def resolution_adaptation(
    rejected: Sequence[TriageDecision],
    target_w: int,
    target_h: int,
    *,
    thresholds: Mapping[str, Any],
    requested_limit: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """How to fit the material when **only** the resolution refused it.

    Returns ``None`` when the refusals are of another kind (too short, duplicate,
    noisy...): those are not a canvas problem and raising a limit would invent a
    selection. When every refusal is ``RESOLUTION_TOO_LOW`` the decision is
    arithmetic, and it is written down either way:

    * the needed growth is at most ``triage_adapt_max_upscale`` -> keep the
      requested canvas and raise the limit to exactly what the material needs;
    * otherwise lower the canvas to the highest standard whose growth stays
      inside ``triage_adapt_target_upscale``.

    If even the smallest standard exceeds the ceiling the material cannot reach a
    deliverable size; the caller then keeps the original refusals, so the error
    can name the number instead of a guess.
    """
    reasons = {item.rejection_reason for item in rejected}
    if reasons != {"RESOLUTION_TOO_LOW"}:
        return None
    sizes = []
    for item in rejected:
        width = int(item.details.get("src_w") or 0)
        height = int(item.details.get("src_h") or 0)
        if width > 0 and height > 0:
            sizes.append((width, height))
    if not sizes:
        return None

    ceiling = float(thresholds["triage_adapt_max_upscale"])
    aim = float(thresholds["triage_adapt_target_upscale"])
    limit_before = float(
        thresholds["triage_max_upscale"] if requested_limit is None else requested_limit
    )

    def needed_for(width: int, height: int) -> float:
        return max(
            max(width / float(src_w), height / float(src_h)) for src_w, src_h in sizes
        )

    requested = [int(target_w), int(target_h)]
    needed = needed_for(target_w, target_h)
    if needed <= ceiling:
        return {
            "reason": "all_rejections_resolution_too_low",
            "mode": "upscale_limit",
            "needed_upscale": round(needed, 4),
            "max_upscale_used": round(needed, 4),
            # The raw value: the report rounds, the gate compares.
            "limit": float(needed),
            "max_upscale_ceiling": ceiling,
            "target_upscale": aim,
            "requested_limit": limit_before,
            "requested_resolution": requested,
            "resolution": requested,
            "ladder": [list(pair) for pair in resolution_ladder(target_w, target_h)],
            "sources": [list(pair) for pair in sorted(set(sizes))],
        }

    ladder = resolution_ladder(target_w, target_h)
    chosen: Optional[Tuple[int, int, float]] = None
    for width, height in ladder[1:]:
        value = needed_for(width, height)
        if value <= aim:
            chosen = (width, height, value)
            break
    if chosen is None:
        width, height = ladder[-1]
        value = needed_for(width, height)
        if value > ceiling:
            return None
        chosen = (width, height, value)
        within_target = False
    else:
        within_target = True
    width, height, value = chosen
    return {
        "reason": "all_rejections_resolution_too_low",
        "mode": "resolution",
        "needed_upscale": round(needed, 4),
        "max_upscale_used": round(value, 4),
        # The raw value: the report rounds, the gate compares.
        "limit": float(value),
        "max_upscale_ceiling": ceiling,
        "target_upscale": aim,
        "within_target_upscale": within_target,
        "requested_limit": limit_before,
        "requested_resolution": requested,
        "resolution": [int(width), int(height)],
        "ladder": [list(pair) for pair in ladder],
        "sources": [list(pair) for pair in sorted(set(sizes))],
    }


def reason_counts(rejected: Sequence[TriageDecision]) -> Dict[str, int]:
    """Refusals by reason: the numbers an error message has to name."""
    counts: Dict[str, int] = {}
    for item in rejected:
        key = str(item.rejection_reason or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _adaptation_warning(adapted: Mapping[str, Any], recovered: int) -> str:
    """One readable line: what the canvas asked, what it now costs, what came back."""
    requested = "x".join(str(item) for item in adapted["requested_resolution"])
    resolution = "x".join(str(item) for item in adapted["resolution"])
    needed = float(adapted["needed_upscale"])
    used = float(adapted["max_upscale_used"])
    sources = ", ".join(
        "x".join(str(item) for item in pair) for pair in adapted.get("sources") or []
    )
    if adapted["mode"] == "upscale_limit":
        return (
            f"triage: {requested} chiedeva un upscale di {needed:.2f}x ({sources}) "
            f"oltre il limite {float(adapted['requested_limit']):.2f}x; limite alzato "
            f"a {used:.2f}x (tetto {float(adapted['max_upscale_ceiling']):.2f}x), "
            f"{recovered} asset recuperati"
        )
    return (
        f"triage: {requested} chiedeva un upscale di {needed:.2f}x ({sources}) oltre "
        f"il tetto {float(adapted['max_upscale_ceiling']):.2f}x; risoluzione di uscita "
        f"abbassata a {resolution} (upscale {used:.2f}x, obiettivo "
        f"{float(adapted['target_upscale']):.2f}x), {recovered} asset recuperati"
    )


def triage_assets(
    assets: Sequence[AssetInfo],
    target_resolution: Any,
    *,
    max_broll_count: Optional[int] = None,
    max_music_count: Optional[int] = None,
    max_upscale: Optional[float] = None,
    require_anchor: bool = False,
    measure: Optional[Callable[[AssetInfo], Mapping[str, Any]]] = measure_asset,
    role_source: Optional[str] = None,
    montage: bool = False,
    fingerprint: Callable[[AssetInfo], str] = perceptual_asset_fingerprint,
) -> TriageResult:
    thresholds = _effective_thresholds()
    target_w, target_h = _target_size(target_resolution)
    upscale_limit = (
        float(thresholds["triage_max_upscale"])
        if max_upscale is None
        else float(max_upscale)
    )
    broll_cap = (
        int(thresholds["triage_max_broll"])
        if max_broll_count is None
        else int(max_broll_count)
    )
    music_cap = (
        int(thresholds["triage_max_music"])
        if max_music_count is None
        else int(max_music_count)
    )
    low = float(thresholds["triage_confidence_low"])
    high = float(thresholds["triage_confidence_high"])

    ordered = sorted(assets, key=lambda item: item.asset_id.lower())
    if role_source is None:
        role_source = (
            "manifest"
            if any(item.role is not None for item in ordered)
            else "heuristic"
        )
    kinds = {item.asset_id: item.kind for item in ordered}

    rejected: List[TriageDecision] = []
    alive: List[AssetInfo] = []

    for asset in ordered:
        if asset.kind not in KINDS:
            rejected.append(
                _decision(
                    asset,
                    role="",
                    selected=False,
                    reason="UNREADABLE",
                    details={
                        "probe_error": f"tipo non riconosciuto: {asset.kind!r}",
                        "confidence": 1.0,
                    },
                )
            )
            continue
        if asset.metrics.get("probe_error"):
            rejected.append(
                _decision(
                    asset,
                    role="",
                    selected=False,
                    reason="UNREADABLE",
                    details={
                        "probe_error": str(asset.metrics["probe_error"]),
                        "confidence": 1.0,
                    },
                )
            )
            continue
        alive.append(asset)

    groups: Dict[Tuple[int, str], List[AssetInfo]] = {}
    for asset in alive:
        if asset.size > 0 and asset.sha256:
            groups.setdefault((asset.size, asset.sha256), []).append(asset)
    duplicate_of: Dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        first = group[0]
        for later in group[1:]:
            duplicate_of[later.asset_id] = first.asset_id

    not_evaluated: set[str] = set()
    deduped: List[AssetInfo] = []
    for asset in alive:
        if asset.asset_id in duplicate_of:
            rejected.append(
                _decision(
                    asset,
                    role="",
                    selected=False,
                    reason="DUPLICATE_ASSET",
                    details={
                        "duplicate_of": duplicate_of[asset.asset_id],
                        "method": "sha256",
                        "size": int(asset.size),
                        "confidence": 1.0,
                    },
                )
            )
            continue
        deduped.append(asset)
    alive = deduped

    near_duplicates = _perceptual_duplicates(
        alive,
        fingerprint=fingerprint,
        thresholds=thresholds,
        not_evaluated=not_evaluated,
    )
    if near_duplicates:
        kept: List[AssetInfo] = []
        for asset in alive:
            match = near_duplicates.get(asset.asset_id)
            if match is None:
                kept.append(asset)
                continue
            rejected.append(
                _decision(
                    asset,
                    role="",
                    selected=False,
                    reason="DUPLICATE_ASSET",
                    details={**match, "confidence": 1.0},
                )
            )
        alive = kept

    base_rejected: List[TriageDecision] = list(rejected)
    base_not_evaluated: set[str] = set(not_evaluated)
    # The output order follows discovery: the manifest when there is one, the
    # file name otherwise. A pool built by hand carries no ``order_index``, so it
    # keeps the alphabetical order triage has always returned. The checks below
    # iterate alphabetically either way, so dedup and role choice stay
    # deterministic whatever the caller's order is.
    rank = {
        asset.asset_id: (
            asset.order_index if asset.order_index >= 0 else len(assets),
            asset.asset_id.lower(),
        )
        for asset in assets
    }

    def _evaluate(
        limit: float, canvas_w: int, canvas_h: int
    ) -> Tuple[List[TriageDecision], List[TriageDecision], set[str]]:
        rejected: List[TriageDecision] = list(base_rejected)
        not_evaluated: set[str] = set(base_not_evaluated)
        roles = _assign_roles(
            alive, from_manifest=role_source == "manifest", montage=montage
        )
        candidates: List[TriageDecision] = []

        for asset in alive:
            role = roles.get(asset.asset_id, "b_roll")
            effective = _effective_duration(asset, thresholds)
            if effective < float(thresholds["triage_min_duration_sec"]):
                rejected.append(
                    _decision(
                        asset,
                        role=role,
                        selected=False,
                        reason="TOO_SHORT",
                        details={
                            "effective_duration_sec": round(effective, 3),
                            "threshold": float(thresholds["triage_min_duration_sec"]),
                            "confidence": 1.0,
                        },
                    )
                )
                continue

            if (
                asset.kind in ("video", "image")
                and asset.width > 0
                and asset.height > 0
            ):
                upscale = max(
                    canvas_w / float(asset.width), canvas_h / float(asset.height)
                )
                if upscale > limit:
                    rejected.append(
                        _decision(
                            asset,
                            role=role,
                            selected=False,
                            reason="RESOLUTION_TOO_LOW",
                            details={
                                "upscale": round(upscale, 4),
                                "max_upscale": limit,
                                "src_w": int(asset.width),
                                "src_h": int(asset.height),
                                "target_w": canvas_w,
                                "target_h": canvas_h,
                                "confidence": 1.0,
                            },
                        )
                    )
                    continue

            metrics: Dict[str, Any] = dict(asset.metrics)
            if measure is not None:
                try:
                    metrics.update(dict(measure(asset) or {}))
                except Exception as exc:  # noqa: BLE001 - a failed measurement is absence
                    metrics.pop("sharpness", None)
                    metrics.pop("mean_luma", None)
                    metrics.pop("snr_db", None)
                    metrics.pop("music_ratio", None)
                    metrics["measure_error"] = f"{type(exc).__name__}: {exc}"

            details: Dict[str, Any] = {}
            confidences: List[float] = []
            rejected_here: Optional[TriageDecision] = None

            for check, key, threshold, reason in _metric_specs(asset, thresholds):
                if key not in metrics:
                    not_evaluated.add(check)
                    continue
                try:
                    value = float(metrics[key])
                except (TypeError, ValueError):
                    not_evaluated.add(check)
                    continue
                confidence = _confidence(value, threshold)
                confidences.append(confidence)
                details[key] = round(value, 4)
                if value < threshold and confidence > high:
                    rejected_here = _decision(
                        asset,
                        role=role,
                        selected=False,
                        reason=reason,
                        details={
                            "value": round(value, 4),
                            "threshold": threshold,
                            "confidence": round(confidence, 4),
                        },
                        confidence=confidence,
                    )
                    break

            if rejected_here is not None:
                rejected.append(rejected_here)
                continue

            if role == "b_roll" and require_anchor and not _has_anchor(asset):
                rejected.append(
                    _decision(
                        asset,
                        role=role,
                        selected=False,
                        reason="NO_SEMANTIC_ANCHOR",
                        details={"reason": "no declared anchor", "confidence": 1.0},
                    )
                )
                continue
            if role == "b_roll" and not require_anchor:
                not_evaluated.add("semantic_anchor")

            if not confidences:
                details["signal"] = "none"
            elif any(low <= value <= high for value in confidences):
                details["uncertain"] = True
                details["signal"] = "ambiguous"
                details["confidence"] = round(
                    max(value for value in confidences if low <= value <= high), 4
                )
            elif all(value < low for value in confidences):
                details["signal"] = "none"
            else:
                details["signal"] = "ok"
                details["confidence"] = round(max(confidences), 4)

            treatment = metrics.get(_MANIFEST_TREATMENT) or (
                "ken_burns_zoom_in" if asset.kind == "image" else "none"
            )
            if role == "music":
                default_gain = float(thresholds["triage_music_gain_db"])
            else:
                default_gain = 0.0
            gain = metrics.get(_MANIFEST_DUCKING, default_gain)
            candidates.append(
                _decision(
                    asset,
                    role=role,
                    selected=True,
                    target_duration_sec=_target_duration(asset, role, thresholds),
                    treatment=str(treatment),
                    ducking_db=float(gain),
                    details=details,
                )
            )

        selected: List[TriageDecision] = []
        broll = [item for item in candidates if item.role == "b_roll"]
        broll.sort(
            key=lambda item: (
                0 if _has_anchor(_asset_for(item, alive)) else 1,
                -_resolution_score(_asset_for(item, alive)),
                item.asset_id.lower(),
            )
        )
        broll_ids = {item.asset_id for item in broll}
        for index, item in enumerate(broll):
            if index < broll_cap:
                selected.append(item)
            else:
                asset = _asset_for(item, alive)
                rejected.append(
                    _decision(
                        asset,
                        role="b_roll",
                        selected=False,
                        reason="REDUNDANT_BROLL",
                        details={
                            "rank": index + 1,
                            "cap": broll_cap,
                            "selected_count": min(len(broll), broll_cap),
                        },
                    )
                )

        music = [item for item in candidates if item.role == "music"]
        music.sort(key=lambda item: item.asset_id.lower())
        for index, item in enumerate(music):
            if index < music_cap:
                selected.append(item)
            else:
                asset = _asset_for(item, alive)
                rejected.append(
                    _decision(
                        asset,
                        role="music",
                        selected=False,
                        reason="REDUNDANT_MUSIC",
                        details={
                            "rank": index + 1,
                            "cap": music_cap,
                            "selected_count": min(len(music), music_cap),
                        },
                    )
                )

        for item in candidates:
            if item.asset_id in broll_ids or item.role == "music":
                continue
            selected.append(item)

        return selected, rejected, not_evaluated

    selected, rejected, not_evaluated = _evaluate(upscale_limit, target_w, target_h)

    adapted: Optional[Dict[str, Any]] = None
    if not any(item.role == "a_roll" for item in selected) and rejected:
        # The machine adapts to the material instead of refusing it, but only
        # when the refusal is a canvas problem and nothing else: a short or
        # duplicated asset is not recovered by moving the resolution. The
        # trigger is "no a_roll": a music bed alone is not a timeline.
        planned = resolution_adaptation(
            rejected,
            target_w,
            target_h,
            thresholds=thresholds,
            requested_limit=upscale_limit,
        )
        if planned is not None:
            width, height = (int(value) for value in planned["resolution"])
            trial = _evaluate(
                float(planned.get("limit", planned["max_upscale_used"])),
                width,
                height,
            )
            trial_selected, trial_rejected, trial_not_evaluated = trial
            planned = dict(planned)
            planned["assets_before"] = len(selected)
            planned["assets_recovered"] = max(
                0, len(trial_selected) - len(selected)
            )
            planned["still_rejected"] = reason_counts(trial_rejected)
            planned["warning"] = _adaptation_warning(
                planned, planned["assets_recovered"]
            )
            adapted = planned
            selected, rejected, not_evaluated = (
                trial_selected,
                trial_rejected,
                trial_not_evaluated,
            )

    def _rank(item: TriageDecision) -> Tuple[int, str]:
        return rank.get(item.asset_id, (len(assets), item.asset_id.lower()))

    selected.sort(key=_rank)
    rejected.sort(key=_rank)
    return TriageResult(
        selected=tuple(selected),
        rejected=tuple(rejected),
        not_evaluated=tuple(sorted(not_evaluated)),
        thresholds=thresholds,
        role_source=role_source,
        kinds=kinds,
        adapted=adapted,
    )


def _asset_for(decision: TriageDecision, assets: Sequence[AssetInfo]) -> AssetInfo:
    for asset in assets:
        if asset.asset_id == decision.asset_id:
            return asset
    return AssetInfo(asset_id=decision.asset_id, kind="video", path="")


# --------------------------------------------------------------------------- #
# payload validation
# --------------------------------------------------------------------------- #


def validate_payload_block(block: Mapping[str, Any]) -> List[str]:
    """Return ``field.path: message`` strings; empty means the block is sound."""
    errors: List[str] = []
    if not isinstance(block, Mapping):
        return ["payload: not a mapping"]

    selected = block.get("selected_assets")
    if not isinstance(selected, list):
        errors.append("selected_assets: must be a list")
    else:
        for index, entry in enumerate(selected):
            if not isinstance(entry, Mapping):
                errors.append(f"selected_assets[{index}]: not an object")
                continue
            role = entry.get("role")
            if not isinstance(role, str) or not role:
                errors.append(f"selected_assets[{index}].role: missing role")
            if not isinstance(entry.get("asset_id"), str) or not entry.get("asset_id"):
                errors.append(f"selected_assets[{index}].asset_id: missing id")

    rejected = block.get("rejected_assets")
    if not isinstance(rejected, list):
        errors.append("rejected_assets: must be a list")
    else:
        for index, entry in enumerate(rejected):
            if not isinstance(entry, Mapping):
                errors.append(f"rejected_assets[{index}]: not an object")
                continue
            reason = entry.get("rejection_reason")
            if reason not in REJECTIONS:
                errors.append(
                    f"rejected_assets[{index}].rejection_reason: unknown reason {reason!r}"
                )
            details = entry.get("details")
            if not isinstance(details, Mapping):
                errors.append(f"rejected_assets[{index}].details: missing details")
            elif not details:
                errors.append(f"rejected_assets[{index}].details: empty details")

    not_evaluated = block.get("not_evaluated")
    if not isinstance(not_evaluated, list):
        errors.append("not_evaluated: must be a list")
    else:
        for index, name in enumerate(not_evaluated):
            if not isinstance(name, str) or not name:
                errors.append(f"not_evaluated[{index}]: empty string")
    return errors
