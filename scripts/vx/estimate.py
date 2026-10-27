"""vx.estimate — how long will this render take?

A render-time predictor fitted from this machine's own past renders, because a
wall-clock number made up by the code is worse than no number: the caller
starts a 40-minute encode believing it will take five.

History is JSONL, one render per line::

    {"width": 1920, "height": 1080, "fps": 30, "duration_sec": 42.0,
     "crf": 18, "preset": "medium", "x264_vs_hw": "x264", "seconds": 61.2}

The model is medians, not regressions. Every row is normalised to a reference
workload (1920x1080 at 30 fps) and to an output second::

    rate_ref = seconds / duration_sec / (width*height*fps / REFERENCE_WORKLOAD)

Rows are bucketed by (codec class, preset class); each bucket keeps the median,
minimum and maximum of its normalised rate, plus n. Prediction for a job is
``rate_ref * duration_sec * workload_ratio(job)``, which is linear in duration
and therefore monotonic by construction. Lookup order is exact bucket -> codec
-> global; every fallback says so in the confidence note. ``crf`` is recorded
but not modelled: the history here has no crf spread to fit, and pretending
otherwise would be an invented coefficient. With no usable history at all the
predictor returns the declared midpoint of ``DEFAULT_RANGE_SEC`` and says
``no_history``.

Fitting and prediction are pure functions over plain dicts; nothing imports
scikit-learn and the model survives ``json.dumps``.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MODEL_VERSION = 1
REFERENCE_WORKLOAD = 1920.0 * 1080.0 * 30.0
DEFAULT_RANGE_SEC: Tuple[float, float] = (15.0, 120.0)
DEFAULT_SECONDS: float = round((DEFAULT_RANGE_SEC[0] + DEFAULT_RANGE_SEC[1]) / 2.0, 3)
HISTORY_ENV = "VX_RENDER_HISTORY"
HISTORY_FILENAME = "render_history.jsonl"

_PRESET_CLASSES = {
    "ultrafast": "fast",
    "superfast": "fast",
    "veryfast": "fast",
    "faster": "fast",
    "fast": "fast",
    "medium": "medium",
    "slow": "slow",
    "slower": "slow",
    "veryslow": "slow",
    "placebo": "slow",
}

_HW_MARKERS = ("nvenc", "qsv", "amf", "videotoolbox", "vaapi", "hw", "gpu")

__all__ = [
    "MODEL_VERSION",
    "REFERENCE_WORKLOAD",
    "DEFAULT_RANGE_SEC",
    "DEFAULT_SECONDS",
    "estimate",
    "fit",
    "predict",
    "record",
    "load_history",
    "job_from_plan",
    "begin",
    "finish",
    "default_history_path",
]


# ------------------------------------------------------------------ helpers


def _codec_class(value: Any) -> str:
    text = str(value or "x264").strip().lower()
    return "hw" if any(marker in text for marker in _HW_MARKERS) else "x264"


def _preset_class(value: Any) -> str:
    return _PRESET_CLASSES.get(str(value or "medium").strip().lower(), "medium")


def _number(row: Mapping[str, Any], key: str) -> Optional[float]:
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0:
        return None
    return value


def _workload_ratio(job: Mapping[str, Any]) -> float:
    """Pixels per second relative to 1080p30; 1.0 means the reference job."""
    width = _number(job, "width") or 0.0
    height = _number(job, "height") or 0.0
    fps = _number(job, "fps") or 0.0
    if width <= 0 or height <= 0 or fps <= 0:
        return 1.0
    return (width * height * fps) / REFERENCE_WORKLOAD


def _bucket_key(codec: str, preset: str) -> str:
    return f"{codec}|{preset}"


def _bucket_stats(rates: List[float]) -> Dict[str, Any]:
    if not rates:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {
        "n": len(rates),
        "median": round(float(statistics.median(rates)), 6),
        "min": round(float(min(rates)), 6),
        "max": round(float(max(rates)), 6),
    }


def _iter_rows(history: Any) -> List[Dict[str, Any]]:
    """Accept a path, JSONL text, or an iterable of row mappings."""
    if history is None:
        return []
    if isinstance(history, Mapping):
        raise TypeError("history: serve una riga per render, non un singolo oggetto")
    if isinstance(history, (str, Path)):
        text = str(history)
        candidate = Path(text)
        if "\n" not in text and candidate.exists():
            return load_history(candidate)
        text = text.strip()
        if not text:
            return []
    else:
        try:
            items = list(history)
        except TypeError as exc:
            raise TypeError(f"history non iterabile: {history!r}") from exc
        return [dict(item) for item in items if isinstance(item, Mapping)]

    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


# ------------------------------------------------------------------ fitting


def fit(history: Any) -> Dict[str, Any]:
    """Fit the median-per-bucket model from JSONL rows, a path, or a list."""
    buckets: Dict[str, List[float]] = {}
    codecs: Dict[str, List[float]] = {}
    rates: List[float] = []
    skipped = 0

    for row in _iter_rows(history):
        duration = _number(row, "duration_sec")
        seconds = _number(row, "seconds")
        if duration is None or seconds is None:
            skipped += 1
            continue
        if any(_number(row, key) is None for key in ("width", "height", "fps")):
            skipped += 1
            continue
        ratio = _workload_ratio(row)
        rate = seconds / duration / ratio
        if rate <= 0 or rate != rate:
            skipped += 1
            continue
        key = _bucket_key(
            _codec_class(row.get("x264_vs_hw")), _preset_class(row.get("preset"))
        )
        buckets.setdefault(key, []).append(rate)
        codecs.setdefault(key.split("|", 1)[0], []).append(rate)
        rates.append(rate)

    model: Dict[str, Any] = {
        "version": MODEL_VERSION,
        "n": len(rates),
        "skipped": skipped,
        "reference_workload": round(REFERENCE_WORKLOAD, 3),
        "buckets": {key: _bucket_stats(values) for key, values in buckets.items()},
        "codecs": {key: _bucket_stats(values) for key, values in codecs.items()},
        "global": _bucket_stats(rates),
    }
    return model


# ---------------------------------------------------------------- predicting


def _pick(model: Mapping[str, Any], job: Mapping[str, Any]) -> Tuple[float, str]:
    codec = _codec_class(job.get("x264_vs_hw"))
    preset = _preset_class(job.get("preset"))
    key = _bucket_key(codec, preset)

    buckets = model.get("buckets") or {}
    exact = buckets.get(key) or {}
    if exact.get("n"):
        return float(exact["median"]), f"bucket={key} n={exact['n']}" + _spread(
            exact, "bucket"
        )

    codecs = model.get("codecs") or {}
    by_codec = codecs.get(codec) or {}
    if by_codec.get("n"):
        return (
            float(by_codec["median"]),
            f"bucket={key} assente, fallback codec={codec} n={by_codec['n']}"
            + _spread(by_codec, "codec"),
        )

    overall = model.get("global") or {}
    if overall.get("n"):
        return (
            float(overall["median"]),
            f"bucket={key} assente, fallback global n={overall['n']}"
            + _spread(overall, "global"),
        )
    raise LookupError("model senza osservazioni")


def _spread(stats: Mapping[str, Any], label: str) -> str:
    low, high, median = stats.get("min"), stats.get("max"), stats.get("median")
    if median and low is not None and high is not None and float(median) > 0:
        spread = (float(high) - float(low)) / float(median)
        return f" {label}_spread={spread:.2f}"
    return ""


def predict(model: Mapping[str, Any], job: Mapping[str, Any]) -> Tuple[float, str]:
    """Predict seconds for ``job``; second value is the confidence note."""
    history_count = int(model.get("n") or 0)
    if history_count <= 0:
        note = (
            f"no_history: default range {DEFAULT_RANGE_SEC[0]:.1f}-"
            f"{DEFAULT_RANGE_SEC[1]:.1f}s, midpoint {DEFAULT_SECONDS:.1f}s"
        )
        return DEFAULT_SECONDS, note

    duration = _number(job, "duration_sec")
    if duration is None:
        raise ValueError("job senza 'duration_sec' positivo")
    try:
        rate, basis = _pick(model, job)
    except LookupError:
        note = (
            f"no_history: default range {DEFAULT_RANGE_SEC[0]:.1f}-"
            f"{DEFAULT_RANGE_SEC[1]:.1f}s, midpoint {DEFAULT_SECONDS:.1f}s"
        )
        return DEFAULT_SECONDS, note

    seconds = rate * duration * _workload_ratio(job)
    note = f"{basis} model_v{model.get('version', MODEL_VERSION)}"
    return round(float(seconds), 3), note


def estimate(job: Mapping[str, Any], history: Any = None) -> Dict[str, Any]:
    """Fit from ``history`` and predict in one call; returns a report fragment."""
    model = fit(history)
    seconds, note = predict(model, job)
    return {
        "predicted_seconds": seconds,
        "confidence": note,
        "history_count": int(model.get("n") or 0),
        "history_skipped": int(model.get("skipped") or 0),
        "model_version": int(model.get("version") or MODEL_VERSION),
    }


# ------------------------------------------------------------------ recording


def load_history(path: Any = None) -> List[Dict[str, Any]]:
    target = Path(str(path)) if path else None
    if target is None or not target.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def record(history_path: Any, job: Mapping[str, Any], seconds: float) -> Dict[str, Any]:
    """Append one finished render to the JSONL history and return the row."""
    target = Path(str(history_path))
    target.parent.mkdir(parents=True, exist_ok=True)
    row: Dict[str, Any] = {
        "width": int(job.get("width") or 0),
        "height": int(job.get("height") or 0),
        "fps": float(job.get("fps") or 0.0),
        "duration_sec": round(float(job.get("duration_sec") or 0.0), 3),
        "crf": job.get("crf"),
        "preset": str(job.get("preset") or "medium"),
        "x264_vs_hw": _codec_class(job.get("x264_vs_hw")),
        "seconds": round(float(seconds), 3),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# ------------------------------------------------------- auto.py integration


def default_history_path(workdir: Any = None) -> Path:
    """Env override, else the workdir, so history survives under --workdir."""
    override = os.environ.get(HISTORY_ENV)
    if override:
        return Path(override)
    if workdir is not None:
        return Path(str(workdir)) / HISTORY_FILENAME
    return Path(HISTORY_FILENAME)


def job_from_plan(
    plan: Any,
    *,
    crf: Any = None,
    preset: Any = "medium",
    x264_vs_hw: Any = "x264",
) -> Dict[str, Any]:
    """Build a job dict from an EditPlan without importing it."""
    resolution: Sequence[Any] = getattr(plan, "resolution", (0, 0))
    return {
        "width": int(resolution[0]) if len(resolution) > 0 else 0,
        "height": int(resolution[1]) if len(resolution) > 1 else 0,
        "fps": float(getattr(plan, "fps", 0.0) or 0.0),
        "duration_sec": float(getattr(plan, "duration_sec", 0.0) or 0.0),
        "crf": crf,
        "preset": str(preset or "medium"),
        "x264_vs_hw": _codec_class(x264_vs_hw),
    }


def begin(
    plan: Any,
    workdir: Any = None,
    *,
    crf: Any = None,
    preset: Any = "medium",
    x264_vs_hw: Any = "x264",
    history_path: Any = None,
) -> Dict[str, Any]:
    """Predict before the render starts; never raises on a broken history.

    Returns the ``render_estimate`` fragment for the report, holding a private
    ``_started`` monotonic mark that ``finish`` consumes. A history file that
    cannot be read degrades to ``no_history`` plus an explicit note instead of
    failing a render over bookkeeping.
    """
    job = job_from_plan(plan, crf=crf, preset=preset, x264_vs_hw=x264_vs_hw)
    path = Path(str(history_path)) if history_path else default_history_path(workdir)
    history_note = None
    try:
        rows = load_history(path)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not break a render
        rows = []
        history_note = f"history_unreadable: {type(exc).__name__}"
    try:
        fragment = estimate(job, rows)
    except Exception as exc:  # noqa: BLE001
        fragment = {
            "predicted_seconds": DEFAULT_SECONDS,
            "confidence": (
                f"no_history: default range {DEFAULT_RANGE_SEC[0]:.1f}-"
                f"{DEFAULT_RANGE_SEC[1]:.1f}s, midpoint {DEFAULT_SECONDS:.1f}s"
            ),
            "history_count": 0,
            "history_skipped": 0,
            "model_version": MODEL_VERSION,
        }
        history_note = f"history_unusable: {type(exc).__name__}"
    fragment.update(
        {
            "job": job,
            "history": str(path),
            "actual_seconds": None,
            "error_ratio": None,
            "_started": time.monotonic(),
        }
    )
    if history_note:
        fragment["history_note"] = history_note
    return fragment


def finish(fragment: Dict[str, Any], history_path: Any = None) -> Dict[str, Any]:
    """Stamp the actual render seconds, append to history, drop private keys."""
    started = fragment.pop("_started", None)
    if started is None:
        fragment.setdefault("actual_seconds", None)
        return fragment
    actual = round(max(0.0, time.monotonic() - float(started)), 3)
    fragment["actual_seconds"] = actual
    predicted = fragment.get("predicted_seconds")
    try:
        fragment["error_ratio"] = (
            round(actual / float(predicted), 3) if predicted else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        fragment["error_ratio"] = None

    job = fragment.get("job") or {}
    target = history_path or fragment.get("history") or default_history_path()
    try:
        record(target, job, actual)
    except Exception as exc:  # noqa: BLE001 - the render already succeeded
        fragment["history_write_error"] = f"{type(exc).__name__}: {exc}"
    return fragment
