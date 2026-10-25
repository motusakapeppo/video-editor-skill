"""vx.faces — face detection, tracking and speaker selection.

YuNet (``cv2.FaceDetectorYN``) is the PRIMARY detector: it is the only one that
can load ``assets/models/face_detection_yunet_2023mar.onnx``. MediaPipe is
OPTIONAL and sits behind a capability check that looks for a ``.task`` bundle —
the MediaPipe Tasks API cannot load the YuNet ONNX, so an import check alone
would lie. When the bundle is absent MediaPipe is skipped silently.

``cv2`` is imported lazily: ``import vx.autoframe`` must work on a machine with
no OpenCV installed.

Tracking / speaker-selection constants come from autoclip (MIT), already
validated. Thresholds that belong to the frozen contract are read from
``vx.contract``; the ones introduced here are declared at module top.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contract import (
    SAMPLE_FPS_FACE,
    FaceTracks,
    MissingDependency,
    VxError,
    clamp,
)

# --------------------------------------------------------------------------- #
# constants introduced by this module (not part of the frozen contract)
# --------------------------------------------------------------------------- #

SKILL_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = SKILL_ROOT / "assets" / "models"
YUNET_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
MEDIAPIPE_MODEL = MODEL_DIR / "face_landmarker.task"

IOU_MATCH = 0.25  # autoclip: same face if IoU >= 0.25 ...
CENTRE_MATCH_K = 1.2  # ... or centre distance <= 1.2 * max(prev_w, cur_w)
MAX_GAP = 0.8  # seconds a track may go undetected before it is closed
MIN_TRACK = 0.5  # seconds; shorter tracks are dropped
MAR_EPS = 1e-6  # guard against a zero-width mouth
SPEAKER_MIN_ACTIVITY = 0.0004  # pvariance(MAR) below this -> no clear speaker
SPEAKER_MARGIN = 1.6  # best score must beat the runner-up by this factor
YUNET_NMS = 0.3
YUNET_TOP_K = 5000
DEFAULT_MAX_WIDTH = 640

# YuNet row layout (15 floats):
#   0 x, 1 y, 2 w, 3 h,
#   4 re_x, 5 re_y, 6 le_x, 7 le_y,
#   8 nose_x, 9 nose_y,
#  10 rmouth_x, 11 rmouth_y, 12 lmouth_x, 13 lmouth_y,
#  14 score
_YUNET_SCORE = 14
_YUNET_RE_Y = 5
_YUNET_LE_Y = 7
_YUNET_RMOUTH_X = 10
_YUNET_RMOUTH_Y = 11
_YUNET_LMOUTH_X = 12
_YUNET_LMOUTH_Y = 13


# --------------------------------------------------------------------------- #
# capability probes
# --------------------------------------------------------------------------- #


def _cv2():
    """Lazy OpenCV import. Returns the module or None."""
    try:
        import cv2  # noqa: PLC0415  (deliberately lazy)
    except Exception:
        return None
    return cv2


def yunet_available() -> bool:
    """True when OpenCV exposes FaceDetectorYN *and* the ONNX asset is present."""
    cv2 = _cv2()
    if cv2 is None or not hasattr(cv2, "FaceDetectorYN"):
        return False
    return YUNET_MODEL.is_file()


def mediapipe_available() -> bool:
    """True only when a MediaPipe ``.task`` bundle exists and mediapipe imports.

    The YuNet ONNX is NOT a valid MediaPipe Tasks bundle, so the file check is
    the real gate; the import check only avoids a pointless import.
    """
    if not MEDIAPIPE_MODEL.is_file():
        return False
    try:
        import mediapipe  # noqa: F401,PLC0415
    except Exception:
        return False
    return True


def model_path() -> str:
    """Absolute path of the YuNet ONNX. Raises when the asset is missing."""
    if not YUNET_MODEL.is_file():
        raise MissingDependency(
            f"YuNet model missing: {YUNET_MODEL} "
            "(expected assets/models/face_detection_yunet_2023mar.onnx)"
        )
    return str(YUNET_MODEL)


# --------------------------------------------------------------------------- #
# small geometry helpers
# --------------------------------------------------------------------------- #


def _iou(a: Dict[str, float], b: Dict[str, float]) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
    iy = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
    inter = ix * iy
    if inter <= 0.0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _centre(box: Dict[str, float]) -> Tuple[float, float]:
    return box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0


def _centre_dist(a: Dict[str, float], b: Dict[str, float]) -> float:
    ax, ay = _centre(a)
    bx, by = _centre(b)
    return math.hypot(ax - bx, ay - by)


def _mar(row: Sequence[float]) -> float:
    """MAR = |lmouth_y - rmouth_y| / |x_right_corner - x_left_corner|.

    Scale invariant, so it is computed on the detector-resolution row.
    """
    rmx = float(row[_YUNET_RMOUTH_X])
    rmy = float(row[_YUNET_RMOUTH_Y])
    lmx = float(row[_YUNET_LMOUTH_X])
    lmy = float(row[_YUNET_LMOUTH_Y])
    denom = abs(lmx - rmx)
    if denom < MAR_EPS:
        return 0.0
    return abs(lmy - rmy) / denom


def _pvariance(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / n


def _centrality(cx: float, cy: float, src_w: float, src_h: float) -> float:
    """1.0 dead centre, 0.0 in a corner."""
    if src_w <= 0 or src_h <= 0:
        return 0.0
    dx = abs(cx - src_w / 2.0) / (src_w / 2.0)
    dy = abs(cy - src_h / 2.0) / (src_h / 2.0)
    return clamp(1.0 - math.hypot(dx, dy) / math.sqrt(2.0), 0.0, 1.0)


# --------------------------------------------------------------------------- #
# video metadata
# --------------------------------------------------------------------------- #


def _video_meta(path: Path) -> Tuple[int, int, float, float]:
    """(width, height, fps, duration) — ffprobe first, OpenCV as fallback."""
    try:
        from .probe import probe  # noqa: PLC0415

        p = probe(path)
        if p.width and p.height:
            return p.width, p.height, float(p.fps or 0.0), float(p.duration or 0.0)
    except Exception:
        pass
    cv2 = _cv2()
    if cv2 is None:
        return 0, 0, 0.0, 0.0
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return 0, 0, 0.0, 0.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        n = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        return w, h, fps, (n / fps if fps > 0 else 0.0)
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
# detectors
# --------------------------------------------------------------------------- #


def _make_yunet(cv2, width: int, height: int, min_conf: float):
    return cv2.FaceDetectorYN.create(
        model_path(),
        "",
        (int(width), int(height)),
        score_threshold=float(min_conf),
        nms_threshold=YUNET_NMS,
        top_k=YUNET_TOP_K,
    )


def _mediapipe_boxes(frame, min_conf: float) -> List[Dict[str, float]]:
    """Optional MediaPipe FaceLandmarker path. Never raises: returns [] on any
    failure. Untested in this environment (no ``.task`` bundle present)."""
    try:
        import mediapipe as mp  # noqa: PLC0415
        from mediapipe.tasks import python as mp_python  # noqa: PLC0415
        from mediapipe.tasks.python import vision  # noqa: PLC0415

        base = mp_python.BaseOptions(model_asset_path=str(MEDIAPIPE_MODEL))
        opts = vision.FaceLandmarkerOptions(
            base_options=base, running_mode=vision.RunningMode.IMAGE, num_faces=8
        )
        with vision.FaceLandmarker.create_from_options(opts) as landmarker:
            rgb = frame[:, :, ::-1]
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect(image)
        out: List[Dict[str, float]] = []
        h, w = frame.shape[:2]
        for face in result.face_landmarks or []:
            xs = [p.x * w for p in face]
            ys = [p.y * h for p in face]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                continue
            out.append(
                {
                    "x": x0,
                    "y": y0,
                    "w": x1 - x0,
                    "h": y1 - y0,
                    "eye_y": y0 + (y1 - y0) * 0.38,
                    "mar": 0.0,
                    "score": float(min_conf),
                }
            )
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# tracking
# --------------------------------------------------------------------------- #


def _assign(active: List[Dict[str, Any]], dets: List[Dict[str, Any]]) -> List[int]:
    """Greedy best-first assignment. Returns, per detection, the index into
    ``active`` it was matched to (or -1)."""
    pairs: List[Tuple[float, float, int, int]] = []
    for ti, track in enumerate(active):
        prev = track["samples"][-1]
        for di, det in enumerate(dets):
            iou = _iou(prev, det)
            dist = _centre_dist(prev, det)
            limit = CENTRE_MATCH_K * max(prev["w"], det["w"])
            if iou >= IOU_MATCH or dist <= limit:
                pairs.append((iou, -dist, ti, di))
    pairs.sort(reverse=True)  # highest IoU first, then closest centre
    used_t: set = set()
    used_d: set = set()
    match = [-1] * len(dets)
    for _iou_v, _neg_dist, ti, di in pairs:
        if ti in used_t or di in used_d:
            continue
        used_t.add(ti)
        used_d.add(di)
        match[di] = ti
    return match


def _finalise(
    tracks: List[Dict[str, Any]],
    src_w: int,
    src_h: int,
    total_duration: float,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for track in tracks:
        samples = track["samples"]
        if not samples:
            continue
        start = float(samples[0]["t"])
        end = float(samples[-1]["t"])
        duration_s = end - start
        if duration_s < MIN_TRACK:
            continue
        areas = [s["w"] * s["h"] for s in samples]
        mean_area = sum(areas) / len(areas)
        cx = sum(_centre(s)[0] for s in samples) / len(samples)
        cy = sum(_centre(s)[1] for s in samples) / len(samples)
        centrality = _centrality(cx, cy, src_w, src_h)
        persistence = (
            clamp(duration_s / total_duration, 0.0, 1.0) if total_duration else 0.0
        )
        score = (mean_area**0.5) * (0.5 + 0.5 * centrality) * (0.4 + 0.6 * persistence)
        out.append(
            {
                "id": int(track["id"]),
                "start": round(start, 4),
                "end": round(end, 4),
                "samples": samples,
                "mean_area": round(mean_area, 3),
                "duration_s": round(duration_s, 4),
                "centrality": round(centrality, 4),
                "score": round(score, 4),
                "persistence": round(persistence, 4),
            }
        )
    return out


def _span_stats(tracks: List[Dict[str, Any]], src_w: int) -> Tuple[float, int]:
    """(span_x_ratio, max_concurrent) from the per-timestamp face centres."""
    by_t: Dict[float, List[float]] = {}
    for track in tracks:
        for s in track["samples"]:
            by_t.setdefault(round(float(s["t"]), 4), []).append(_centre(s)[0])
    if not by_t:
        return 0.0, 0
    max_concurrent = max(len(v) for v in by_t.values())
    widest = 0.0
    for centres in by_t.values():
        if len(centres) >= 2:
            widest = max(widest, max(centres) - min(centres))
    ratio = widest / src_w if src_w > 0 else 0.0
    return clamp(ratio, 0.0, 1.0), max_concurrent


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def detect_faces_tracked(
    path,
    *,
    sample_fps: float = SAMPLE_FPS_FACE,
    min_conf: float = 0.6,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> FaceTracks:
    """Detect faces at ``sample_fps``, link them into tracks, classify framing."""
    src = Path(path)
    if not src.is_file():
        raise MissingDependency(f"input not found: {src}")
    cv2 = _cv2()
    if cv2 is None:
        raise MissingDependency("OpenCV (cv2) is not installed in this interpreter")

    use_yunet = yunet_available()
    use_mediapipe = (not use_yunet) and mediapipe_available()
    if not use_yunet and not use_mediapipe:
        raise MissingDependency(
            "no face detector available: need OpenCV FaceDetectorYN + "
            f"{YUNET_MODEL.name} (or a MediaPipe .task bundle)"
        )

    src_w, src_h, src_fps, duration = _video_meta(src)
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise MissingDependency(f"cannot open video: {src}")
    try:
        if not src_w or not src_h:
            src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if src_fps <= 0:
            src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if src_w <= 0 or src_h <= 0:
            raise VxError(f"unknown frame size for {src.name}")

        # When the container lies about fps we still sample every frame and
        # label it at sample_fps; the plan then carries that rate.
        eff_fps = src_fps if src_fps > 0 else float(sample_fps)
        step = max(1, int(round(eff_fps / float(sample_fps)))) if sample_fps > 0 else 1

        detector = None
        det_w = det_h = 0
        tracks: List[Dict[str, Any]] = []
        active: List[Dict[str, Any]] = []
        next_id = 0
        sampled = 0
        with_face = 0
        last_t = 0.0
        idx = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step:
                idx += 1
                continue
            t = idx / eff_fps
            last_t = t
            sampled += 1
            fh, fw = frame.shape[:2]

            if fw > max_width:
                ratio = max_width / float(fw)
                det_w = int(max_width)
                det_h = max(2, int(round(fh * ratio)))
                small = cv2.resize(frame, (det_w, det_h), interpolation=cv2.INTER_AREA)
            else:
                det_w, det_h = fw, fh
                small = frame

            dets: List[Dict[str, Any]] = []
            if use_yunet:
                if detector is None:
                    detector = _make_yunet(cv2, det_w, det_h, min_conf)
                else:
                    detector.setInputSize((det_w, det_h))
                _ret, faces = detector.detect(small)
                if faces is not None:
                    sx = fw / float(det_w)
                    sy = fh / float(det_h)
                    for row in faces:
                        score = float(row[_YUNET_SCORE])
                        if score < min_conf:
                            continue
                        dets.append(
                            {
                                "t": round(t, 4),
                                "x": float(row[0]) * sx,
                                "y": float(row[1]) * sy,
                                "w": float(row[2]) * sx,
                                "h": float(row[3]) * sy,
                                "eye_y": (
                                    float(row[_YUNET_RE_Y]) + float(row[_YUNET_LE_Y])
                                )
                                / 2.0
                                * sy,
                                "mar": _mar(row),
                                "score": score,
                            }
                        )
            else:
                for box in _mediapipe_boxes(small, min_conf):
                    sx = fw / float(det_w)
                    sy = fh / float(det_h)
                    box = dict(box)
                    box["t"] = round(t, 4)
                    box["x"] *= sx
                    box["y"] *= sy
                    box["w"] *= sx
                    box["h"] *= sy
                    box["eye_y"] *= sy
                    dets.append(box)

            if dets:
                with_face += 1

            match = _assign(active, dets)
            for di, det in enumerate(dets):
                ti = match[di]
                if ti >= 0:
                    active[ti]["samples"].append(det)
                else:
                    track = {"id": next_id, "samples": [det]}
                    next_id += 1
                    tracks.append(track)
                    active.append(track)
            # close tracks that have been dark for longer than MAX_GAP
            active = [
                tr for tr in active if t - float(tr["samples"][-1]["t"]) <= MAX_GAP
            ]
            idx += 1
    finally:
        cap.release()

    total_duration = duration if duration > 0 else (last_t if last_t > 0 else 0.0)
    final = _finalise(tracks, src_w, src_h, total_duration)
    span_ratio, max_concurrent = _span_stats(final, src_w)

    if not final:
        framing = "center"
    elif span_ratio > 0.95:
        framing = "fit"
    elif max_concurrent >= 2:
        framing = "multi"
    else:
        framing = "single"

    return FaceTracks(
        detected_ratio=round(with_face / sampled, 4) if sampled else 0.0,
        tracks=final,
        max_concurrent=max_concurrent,
        framing=framing,
        detector="yunet" if use_yunet else ("mediapipe" if use_mediapipe else "none"),
        span_x_ratio=round(span_ratio, 4),
    )


def mouth_activity(track: dict, start: float, end: float) -> float:
    """Population variance of MAR inside [start, end] — the speech proxy."""
    values = [
        float(s["mar"])
        for s in track.get("samples", [])
        if start <= float(s["t"]) <= end
    ]
    return _pvariance(values)


def select_speaker_track(tracks: list[dict], *, duration: float) -> dict | None:
    """Pick the talking head, or None when the shot has no clear speaker."""
    if not tracks:
        return None
    scored: List[Tuple[float, float, dict]] = []
    for track in tracks:
        activity = mouth_activity(track, track["start"], track["end"])
        scored.append((float(track.get("score", 0.0)), activity, track))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_activity, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_activity < SPEAKER_MIN_ACTIVITY:
        return None
    if runner_up > 0.0 and best_score < SPEAKER_MARGIN * runner_up:
        return None
    return best


# --------------------------------------------------------------------------- #
# smoke entry
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m vx.faces <video> [sample_fps] [min_conf]")
        return 2
    path = args[0]
    sample_fps = float(args[1]) if len(args) > 1 else SAMPLE_FPS_FACE
    min_conf = float(args[2]) if len(args) > 2 else 0.6
    print(
        f"yunet_available={yunet_available()} mediapipe_available={mediapipe_available()}"
    )
    tracks = detect_faces_tracked(path, sample_fps=sample_fps, min_conf=min_conf)
    speaker = select_speaker_track(tracks.tracks, duration=0.0)
    payload = tracks.to_dict()
    payload["speaker_id"] = speaker["id"] if speaker else None
    print(json.dumps(payload, indent=2)[:4000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
