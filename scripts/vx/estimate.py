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

The second half of the module answers a different question: *quanto ci vorra'
per questo piano, e vogliamo aspettare?* ``plan_estimate`` somma gli stadi che
``auto`` cronometra (``probe/ingest/audio/analysis/faces/render/qc``), pesa gli
effetti per shot con ``effects.cost`` e si calibra sulle righe di
``timing_history.jsonl`` (piu' la storia dei render per la parte ``render``).
Senza misure usa costanti dichiarate e scrive ``calibrated: false``.
``plan_gate`` confronta l'estremo alto con il tetto (``--budget``): sotto si
parte, sopra scrive ``plan_choice.json`` e aspetta ``full`` o ``fast``, al
massimo ``--plan-wait``; allo scadere prende la versione rapida e lo dichiara.
``fast_plan`` abbassa la risoluzione di lavoro, usa un preset veloce e riduce
effetti e overlay lasciando intatta la struttura di layer 0, cioe' quella che i
controlli di coerenza guardano.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MODEL_VERSION = 1
REFERENCE_WORKLOAD = 1920.0 * 1080.0 * 30.0
DEFAULT_RANGE_SEC: Tuple[float, float] = (15.0, 120.0)
DEFAULT_SECONDS: float = round((DEFAULT_RANGE_SEC[0] + DEFAULT_RANGE_SEC[1]) / 2.0, 3)
HISTORY_ENV = "VX_RENDER_HISTORY"
HISTORY_FILENAME = "render_history.jsonl"

#: Il cronometro di ``auto`` (per stadio) diventa calibrazione: ogni giro scritto
#: qui misura quanto e' costato davvero ogni stadio su quel materiale. Il file
#: vive nella workdir, come la storia dei render, e si legge da li'.
TIMING_HISTORY_ENV = "VX_TIMING_HISTORY"
TIMING_HISTORY_FILENAME = "timing_history.jsonl"

#: Il cancello della scelta: sopra il tetto si chiede full o fast.
PLAN_CHOICE_NAME = "plan_choice.json"
PLAN_CHOICE_SCHEMA = "vx.plan-choice/1"
BUDGET_DEFAULT = 600.0
PLAN_WAIT_DEFAULT = 180.0
PLAN_CHOICE_VALUES = ("full", "fast")

#: Gli stadi che il piano dichiara in ``parts``: quelli che ``auto`` cronometra
#: e che la stima prezza.
ESTIMATE_STAGES = ("probe", "ingest", "audio", "analysis", "faces", "render", "qc")
MATERIAL_STAGES = ("probe", "ingest", "audio", "analysis", "faces")

#: Costanti dichiarate, usate solo quando non c'e' una misura: secondi di stadio
#: per secondo di materiale (1080p30) e per secondo di master. Non sono una
#: calibrazione e il referto lo dice (``calibrated: false``).
DECLARED_MATERIAL_RATES: Dict[str, float] = {
    "probe": 0.02,
    "ingest": 0.25,
    "audio": 0.08,
    "analysis": 0.15,
    "faces": 0.12,
}
DECLARED_OUTPUT_RATES: Dict[str, float] = {"qc": 0.04}
DECLARED_RENDER_RATE = 0.85
DECLARED_SPREAD = 0.35

#: Il preset cambia il costo del render di un fattore dichiarato, non misurato:
#: vale solo quando il render parte dalle costanti (una misura include il preset
#: che e' stato usato).
PRESET_FACTORS: Dict[str, float] = {"fast": 0.6, "medium": 1.0, "slow": 1.5}

#: Un effetto vale questa quota di secondo di render per secondo di clip quando
#: non e' uno spec componibile (per gli spec si usa ``effects.cost``).
DEFAULT_EFFECT_COST = 1.0
EFFECT_RENDER_WEIGHT = 0.25
EFFECT_DENSITY_CAP = 2.0

#: La versione rapida: risoluzione di lavoro piu' bassa, preset veloce, un solo
#: effetto e un solo overlay per clip. La struttura di layer 0 non cambia, ed e'
#: quella che i controlli di coerenza guardano.
FAST_PRESET = "veryfast"
FAST_SCALE = 2.0 / 3.0
FAST_MIN_SIDE = 320
FAST_MAX_EFFECTS = 1
FAST_MAX_OVERLAYS = 1

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
    "BUDGET_DEFAULT",
    "PLAN_WAIT_DEFAULT",
    "PLAN_CHOICE_NAME",
    "PLAN_CHOICE_SCHEMA",
    "PLAN_CHOICE_VALUES",
    "ESTIMATE_STAGES",
    "FAST_PRESET",
    "TIMING_HISTORY_FILENAME",
    "estimate",
    "fit",
    "predict",
    "record",
    "load_history",
    "job_from_plan",
    "begin",
    "finish",
    "default_history_path",
    "effect_cost",
    "plan_cost",
    "plan_facts",
    "calibrate",
    "plan_estimate",
    "fast_plan",
    "plan_gate",
    "read_plan_choice",
    "load_timings",
    "record_timings",
    "default_timing_path",
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


# ------------------------------------------------------- plan cost (effects)


def _field(item: Any, name: str, default: Any = None) -> Any:
    """Legge un campo da un oggetto o da un mapping, senza imporre l'uno o l'altro."""
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _timeline_field(plan: Any, name: str, default: Any = None) -> Any:
    """I campi della timeline: su un EDLPlan sono attributi, in ``plan.json``
    stanno sotto ``timeline`` (e la dimensione dell'EDL 3 si chiama ``size``)."""
    value = _field(plan, name)
    if value is None and isinstance(plan, Mapping):
        timeline = plan.get("timeline")
        if isinstance(timeline, Mapping):
            value = timeline.get(name)
        if value is None and name == "resolution":
            size = plan.get("size")
            if isinstance(size, (list, tuple)) and len(size) >= 2:
                value = size
    return default if value is None else value


def _positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return number


def video_clips(plan: Any) -> List[Any]:
    """Le clip video del piano (oggetto EDL o mapping di ``plan.json``)."""
    tracks = _field(plan, "video_tracks")
    if not tracks:
        raw = _field(plan, "tracks")
        if isinstance(raw, Mapping):
            tracks = raw.get("video") or ()
        else:
            tracks = raw or ()
    clips: List[Any] = []
    for track in tracks:
        for clip in _field(track, "clips", ()) or ():
            clips.append(clip)
    return clips


def effect_cost(spec: Any) -> float:
    """Il costo di un effetto: ``effects.cost`` per gli spec, altrimenti 1.0.

    Un ``Effect`` dell'EDL 3 non e' uno spec componibile (non ha
    ``primitives``): ``effects.cost`` vale 0.0 e non e' una misura. Per quelli
    si usa ``DEFAULT_EFFECT_COST``, dichiarato, mai spacciato per calibrato.
    """
    if isinstance(spec, Mapping):
        try:
            from . import effects as effects_mod  # noqa: PLC0415 - import pigro

            value = float(effects_mod.cost(spec))
        except Exception:  # noqa: BLE001 - un registro rotto non ferma una stima
            value = 0.0
        if value > 0:
            return round(value, 3)
    return float(DEFAULT_EFFECT_COST)


def plan_cost(plan: Any) -> Dict[str, Any]:
    """Il costo del piano: somma di ``effect_cost`` per shot, per i suoi secondi.

    ``cost_seconds`` e' il numero che pesa: secondi di materiale resi piu'
    cari dagli effetti. ``per_shot`` tiene la provenienza, cosi' il referto puo'
    dire quale clip costa e perche'.
    """
    per_shot: List[Dict[str, Any]] = []
    total_cost = 0.0
    cost_seconds = 0.0
    effects_count = 0
    for clip in video_clips(plan):
        start = _positive(_field(clip, "dst_in")) or 0.0
        end = _positive(_field(clip, "dst_out")) or start
        seconds = max(0.0, float(end) - float(start))
        specs = list(_field(clip, "effects", ()) or ())
        costs = [effect_cost(spec) for spec in specs]
        shot_cost = sum(costs)
        effects_count += len(costs)
        total_cost += shot_cost
        cost_seconds += shot_cost * seconds
        per_shot.append(
            {
                "clip": str(_field(clip, "clip_id", "") or ""),
                "layer": int(_field(clip, "layer", 0) or 0),
                "seconds": round(seconds, 3),
                "effects": len(costs),
                "cost": round(shot_cost, 3),
                "cost_seconds": round(shot_cost * seconds, 3),
            }
        )
    return {
        "shots": len(per_shot),
        "effects": effects_count,
        "cost": round(total_cost, 3),
        "cost_seconds": round(cost_seconds, 3),
        "per_shot": per_shot,
    }


def plan_facts(plan: Any) -> Dict[str, Any]:
    """Le unita' di lavoro del piano: materiale, uscita, risoluzione, effetti."""
    output_sec = _positive(_timeline_field(plan, "duration_sec")) or 0.0
    resolution = list(_timeline_field(plan, "resolution", (0, 0)) or (0, 0))
    width = int(resolution[0]) if len(resolution) > 0 else 0
    height = int(resolution[1]) if len(resolution) > 1 else 0
    fps = float(_positive(_timeline_field(plan, "fps")) or 0.0)

    material = 0.0
    for clip in video_clips(plan):
        src_in = float(_positive(_field(clip, "src_in")) or 0.0)
        src_out = float(_positive(_field(clip, "src_out")) or src_in)
        material += max(0.0, src_out - src_in)
    if material <= 0:
        material = output_sec

    cost = plan_cost(plan)
    workload = 1.0
    if width > 0 and height > 0 and fps > 0:
        workload = (width * height * fps) / REFERENCE_WORKLOAD
    return {
        "output_sec": round(output_sec, 3),
        "material_sec": round(material, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "resolution": [width, height],
        "workload": round(workload, 6),
        "shots": cost["shots"],
        "effects": cost,
        "effect_seconds": cost["cost_seconds"],
        "effect_density": round(
            cost["cost_seconds"] / output_sec if output_sec > 0 else 0.0, 6
        ),
    }


# ------------------------------------------------- timings -> calibration


def default_timing_path(workdir: Any = None) -> Path:
    """Env override, else the workdir: la calibrazione segue il lavoro."""
    override = os.environ.get(TIMING_HISTORY_ENV)
    if override:
        return Path(override)
    if workdir is not None:
        return Path(str(workdir)) / TIMING_HISTORY_FILENAME
    return Path(TIMING_HISTORY_FILENAME)


def _rows_from_file(path: Any) -> List[Dict[str, Any]]:
    target = Path(str(path))
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []
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


def load_timings(path: Any = None) -> List[Dict[str, Any]]:
    """Le misure di ``auto`` (una riga per giro) da un file o da una workdir."""
    if path is None:
        return _rows_from_file(default_timing_path())
    target = Path(str(path))
    if target.is_dir():
        target = target / TIMING_HISTORY_FILENAME
    return _rows_from_file(target)


def calibrate(rows: Any) -> Dict[str, Any]:
    """Da ``timings`` misurati alle tariffe per stadio (secondi per unita').

    Ogni riga e' un giro vero: gli stadi che guardano il materiale si dividono
    per i secondi di materiale, ``qc`` per i secondi di master, ``render`` per
    ``secondi * carico``. Mediana, minimo e massimo per stadio: l'intervallo
    della stima viene da qui, non da una percentuale inventata. Una riga senza
    ``timings`` (o con tempi a zero, cioe' stadio non eseguito) non entra.
    """
    material: Dict[str, List[float]] = {stage: [] for stage in MATERIAL_STAGES}
    output: Dict[str, List[float]] = {"qc": []}
    render: List[float] = []
    skipped = 0
    used = 0

    for row in rows or ():
        if not isinstance(row, Mapping):
            skipped += 1
            continue
        timings = row.get("timings")
        if not isinstance(timings, Mapping):
            skipped += 1
            continue
        material_sec = _positive(row.get("material_sec")) or _positive(
            row.get("timeline_sec")
        )
        output_sec = _positive(row.get("timeline_sec"))
        width = _positive(row.get("width"))
        height = _positive(row.get("height"))
        fps = _positive(row.get("fps"))
        workload = (
            (width * height * fps) / REFERENCE_WORKLOAD
            if width and height and fps
            else 1.0
        )
        measured = 0
        for stage in MATERIAL_STAGES:
            seconds = _positive(timings.get(stage))
            if seconds and material_sec:
                material[stage].append(seconds / material_sec)
                measured += 1
        for stage in output:
            seconds = _positive(timings.get(stage))
            if seconds and output_sec:
                output[stage].append(seconds / output_sec)
                measured += 1
        render_sec = _positive(timings.get("render"))
        if render_sec and output_sec:
            render.append(render_sec / (output_sec * workload))
            measured += 1
        if measured:
            used += 1
        else:
            skipped += 1

    return {
        "n": used,
        "skipped": skipped,
        "material": {stage: _bucket_stats(values) for stage, values in material.items()},
        "output": {stage: _bucket_stats(values) for stage, values in output.items()},
        "render": _bucket_stats(render),
    }


def _spread_of(stats: Mapping[str, Any], fallback: float = DECLARED_SPREAD) -> float:
    """L'incertezza dichiarata da una misura: (max-min)/mediana, con un tetto."""
    low, high, median = stats.get("min"), stats.get("max"), stats.get("median")
    if not (low is not None and high is not None and median):
        return fallback
    try:
        spread = (float(high) - float(low)) / float(median)
    except (TypeError, ValueError, ZeroDivisionError):
        return fallback
    if spread != spread:
        return fallback
    return round(max(0.05, min(1.0, spread)), 4)


# ------------------------------------------------------ the plan estimate


def _part(rate: float, spread: float, unit: float) -> Tuple[float, float, float]:
    seconds = rate * unit
    return (seconds, max(0.0, seconds * (1.0 - spread)), seconds * (1.0 + spread))


def plan_estimate(
    plan: Any,
    *,
    timings: Any = None,
    history: Any = None,
    preset: Any = "medium",
    crf: Any = 18,
    x264_vs_hw: Any = "x264",
) -> Dict[str, Any]:
    """Quanto ci vorra' per questo piano: intervallo, stadi, calibrazione.

    Ordine delle fonti, mai una sola:

    1. ``timings`` misurati (le righe di ``timing_history.jsonl``) danno la
       tariffa per stadio, con il proprio minimo e massimo;
    2. la storia dei render (``render_history.jsonl``) da' la parte ``render``
       quando c'e', perche' e' l'unica che sa risoluzione e preset;
    3. senza misure restano le costanti dichiarate qui sopra, e il referto dice
       ``calibrated: false``: un numero inventato non si traveste da misura.

    Il piano pesa anche per gli effetti: la somma di ``effect_cost`` per shot
    moltiplicata per i secondi alza la parte ``render`` (tetto dichiarato).
    """
    facts = plan_facts(plan)
    model = calibrate(timings)
    hist = fit(history)
    notes: List[str] = []
    parts: Dict[str, float] = {}
    low_parts: Dict[str, float] = {}
    high_parts: Dict[str, float] = {}
    basis: Dict[str, str] = {}

    material_sec = float(facts["material_sec"])
    output_sec = float(facts["output_sec"])

    for stage in MATERIAL_STAGES:
        stats = model["material"].get(stage) or {}
        if stats.get("n"):
            rate = float(stats["median"])
            spread = _spread_of(stats)
            basis[stage] = f"measured n={stats['n']}"
        else:
            rate = DECLARED_MATERIAL_RATES[stage]
            spread = DECLARED_SPREAD
            basis[stage] = "declared"
        parts[stage], low_parts[stage], high_parts[stage] = _part(
            rate, spread, material_sec
        )

    qc_stats = model["output"].get("qc") or {}
    if qc_stats.get("n"):
        rate = float(qc_stats["median"])
        spread = _spread_of(qc_stats)
        basis["qc"] = f"measured n={qc_stats['n']}"
    else:
        rate = DECLARED_OUTPUT_RATES["qc"]
        spread = DECLARED_SPREAD
        basis["qc"] = "declared"
    parts["qc"], low_parts["qc"], high_parts["qc"] = _part(
        rate, spread, output_sec
    )

    preset_class = _preset_class(preset)
    workload = float(facts["workload"])
    density = min(EFFECT_DENSITY_CAP, float(facts["effect_density"]))
    effect_factor = 1.0 + EFFECT_RENDER_WEIGHT * density

    render_stats = model["render"] if model["render"].get("n") else {}
    if int(hist.get("n") or 0) > 0:
        job = {
            "width": facts["width"],
            "height": facts["height"],
            "fps": facts["fps"],
            "duration_sec": max(output_sec, 0.001),
            "crf": crf,
            "preset": str(preset or "medium"),
            "x264_vs_hw": _codec_class(x264_vs_hw),
        }
        predicted, note = predict(hist, job)
        spread = _spread_of(hist.get("global") or {})
        render_rate = predicted / max(output_sec * workload, 0.001)
        basis["render"] = "measured render_history"
        notes.append(f"render da storia: {note} (spread {spread:.2f})")
    elif render_stats:
        render_rate = float(render_stats["median"])
        spread = _spread_of(render_stats)
        basis["render"] = f"measured timings n={render_stats['n']}"
        notes.append(
            "render misurato su giri passati: il preset del giro non e' corretto"
        )
    else:
        render_rate = DECLARED_RENDER_RATE * PRESET_FACTORS.get(preset_class, 1.0)
        spread = DECLARED_SPREAD
        basis["render"] = f"declared preset={preset_class}"

    parts["render"] = render_rate * output_sec * workload * effect_factor
    low_parts["render"] = parts["render"] * (1.0 - spread)
    high_parts["render"] = parts["render"] * (1.0 + spread)

    if not model["n"]:
        notes.append(
            "no_timings: nessuna misura per stadio, costanti dichiarate "
            f"(caricamento {TIMING_HISTORY_FILENAME} vuoto)"
        )
    if not hist.get("n"):
        notes.append(
            "no_render_history: parte render dalle costanti dichiarate, "
            f"preset={preset_class}"
        )
    notes.append(
        "effetti: "
        f"{facts['effects']['effects']} su {facts['shots']} shot, "
        f"costo {facts['effects']['cost']:g} -> {facts['effect_seconds']:g} s "
        f"pesati (densita' {facts['effect_density']:.2f}, fattore {effect_factor:.2f})"
    )
    notes.append(
        "non prezzate: triage, hash, proxy, font, keyframes, deliverables"
    )

    seconds = sum(parts.values())
    low = sum(low_parts.values())
    high = sum(high_parts.values())
    if high <= low:
        low, high = seconds * (1.0 - DECLARED_SPREAD), seconds * (1.0 + DECLARED_SPREAD)

    return {
        "seconds": round(seconds, 3),
        "low": round(min(low, seconds), 3),
        "high": round(max(high, seconds), 3),
        "calibrated": bool(model["n"]),
        "parts": {stage: round(parts[stage], 3) for stage in ESTIMATE_STAGES},
        "notes": notes,
        "basis": basis,
        "preset": str(preset or "medium"),
        "crf": crf,
        "x264_vs_hw": _codec_class(x264_vs_hw),
        "material_sec": facts["material_sec"],
        "output_sec": facts["output_sec"],
        "resolution": list(facts["resolution"]),
        "workload": facts["workload"],
        "effects": {
            "count": facts["effects"]["effects"],
            "cost": facts["effects"]["cost"],
            "cost_seconds": facts["effects"]["cost_seconds"],
            "factor": round(effect_factor, 3),
        },
        "timings_rows": int(model["n"]),
        "render_history_rows": int(hist.get("n") or 0),
    }


# ------------------------------------------------------- the fast version


def _even(value: float, minimum: int = FAST_MIN_SIDE) -> int:
    number = int(round(value))
    number -= number % 2
    return max(minimum if minimum % 2 == 0 else minimum + 1, number)


def fast_resolution(resolution: Sequence[Any], *, scale: float = FAST_SCALE) -> List[int]:
    """La risoluzione di lavoro della versione rapida: piu' bassa, pari, >= 320."""
    values: List[int] = []
    for index in (0, 1):
        try:
            value = float(resolution[index])
        except (IndexError, TypeError, ValueError):
            value = 0.0
        values.append(_even(value * float(scale)) if value > 0 else FAST_MIN_SIDE)
    return values


def _structure_digest(plan: Any) -> str:
    """L'impronta della struttura che i controlli di coerenza guardano.

    Solo il layer 0 (il montaggio): overlay, risoluzione ed effetti non sono
    inquadrature e la coerenza li ignora per costruzione. Se questa impronta
    non cambia, il referto di coerenza resta valido anche per il piano rapido.
    """
    import hashlib

    shots = []
    for clip in video_clips(plan):
        if int(_field(clip, "layer", 0) or 0) != 0:
            continue
        shots.append(
            [
                str(_field(clip, "clip_id", "") or ""),
                str(_field(clip, "source_id", "") or ""),
                round(float(_positive(_field(clip, "src_in")) or 0.0), 6),
                round(float(_positive(_field(clip, "src_out")) or 0.0), 6),
                round(float(_positive(_field(clip, "dst_in")) or 0.0), 6),
                round(float(_positive(_field(clip, "dst_out")) or 0.0), 6),
            ]
        )
    shots.sort()
    payload = {
        "fps": _positive(_timeline_field(plan, "fps")) or 0.0,
        "duration_sec": _positive(_timeline_field(plan, "duration_sec")) or 0.0,
        "shots": shots,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _fast_effects(effects: Sequence[Any]) -> List[Any]:
    return list(effects or ())[:FAST_MAX_EFFECTS]


def fast_plan(
    plan: Any,
    *,
    scale: float = FAST_SCALE,
    preset: Any = FAST_PRESET,
) -> Dict[str, Any]:
    """La versione rapida: stesso montaggio, meno costo.

    Cosa cambia, e solo questo:

    * risoluzione di lavoro piu' bassa (``scale``, pari, >= 320);
    * preset veloce, che arriva al render;
    * al massimo ``FAST_MAX_EFFECTS`` effetti per clip e ``FAST_MAX_OVERLAYS``
      clip di overlay: i candidati decorativi si riducono, il montaggio no.

    La struttura di layer 0 resta identica (``coherence_kept`` lo prova), quindi
    i controlli di coerenza restano validi senza rifare le misure.
    """
    digest_before = _structure_digest(plan)
    resolution = list(_timeline_field(plan, "resolution", (0, 0)) or (0, 0))
    original = [int(v) for v in resolution[:2]] if len(resolution) >= 2 else [0, 0]
    fast_res = fast_resolution(original, scale=scale)
    traded: List[str] = []
    if fast_res != original:
        traded.append(
            f"risoluzione: {original[0]}x{original[1]} -> {fast_res[0]}x{fast_res[1]}"
        )
    traded.append(f"preset: -> {preset} (piu' veloce)")

    dropped_effects = 0
    dropped_overlays = 0
    dropped_overlay_ids: List[str] = []
    new_plan: Any = plan

    if hasattr(plan, "video_tracks"):
        import dataclasses

        new_tracks = []
        overlays_kept = 0
        for track in plan.video_tracks:
            kept = []
            for clip in track.clips:
                layer = int(getattr(clip, "layer", 0) or 0)
                if layer != 0:
                    if overlays_kept >= FAST_MAX_OVERLAYS:
                        dropped_overlays += 1
                        dropped_overlay_ids.append(str(getattr(clip, "clip_id", "") or ""))
                        continue
                    overlays_kept += 1
                effects = list(getattr(clip, "effects", ()) or ())
                trimmed = _fast_effects(effects)
                dropped_effects += len(effects) - len(trimmed)
                if trimmed != effects and dataclasses.is_dataclass(clip):
                    try:
                        clip = dataclasses.replace(clip, effects=tuple(trimmed))
                    except (TypeError, ValueError):
                        pass
                kept.append(clip)
            if dataclasses.is_dataclass(track):
                new_tracks.append(dataclasses.replace(track, clips=tuple(kept)))
            else:
                new_tracks.append(track)
        try:
            new_plan = dataclasses.replace(
                plan, resolution=tuple(fast_res), video_tracks=tuple(new_tracks)
            )
        except (TypeError, ValueError):
            # Un piano vero e' un dataclass; un oggetto minimo di prova no. Si
            # copia e si scrivono i due campi, senza inventare un costruttore.
            try:
                import copy

                new_plan = copy.copy(plan)
                setattr(new_plan, "resolution", tuple(fast_res))
                setattr(new_plan, "video_tracks", tuple(new_tracks))
            except Exception:  # noqa: BLE001 - un piano immutabile resta com'e'
                new_plan = plan
    elif isinstance(plan, Mapping):
        payload = json.loads(json.dumps(plan))
        timeline = payload.get("timeline")
        if isinstance(timeline, Mapping):
            timeline["resolution"] = list(fast_res)
        elif isinstance(payload.get("size"), (list, tuple)):
            payload["size"] = list(fast_res)
        else:
            payload["resolution"] = list(fast_res)
        overlays_kept = 0
        for track in (payload.get("tracks") or {}).get("video") or ():
            clips = []
            for clip in track.get("clips") or ():
                if int(clip.get("layer", 0) or 0) != 0:
                    if overlays_kept >= FAST_MAX_OVERLAYS:
                        dropped_overlays += 1
                        dropped_overlay_ids.append(str(clip.get("clip_id") or ""))
                        continue
                    overlays_kept += 1
                effects = list(clip.get("effects") or ())
                trimmed = _fast_effects(effects)
                dropped_effects += len(effects) - len(trimmed)
                clip["effects"] = trimmed
                clips.append(clip)
            track["clips"] = clips
        new_plan = payload

    if dropped_effects:
        traded.append(
            f"effetti: {dropped_effects} in meno (al massimo {FAST_MAX_EFFECTS} per clip)"
        )
    if dropped_overlays:
        traded.append(
            f"overlay: {dropped_overlays} in meno (al massimo {FAST_MAX_OVERLAYS})"
            + (f": {', '.join(i for i in dropped_overlay_ids if i)}" if any(dropped_overlay_ids) else "")
        )

    digest_after = _structure_digest(new_plan)
    return {
        "plan": new_plan,
        "preset": str(preset),
        "resolution": fast_res,
        "traded": traded,
        "dropped_effects": dropped_effects,
        "dropped_overlays": dropped_overlays,
        "dropped_overlay_ids": [i for i in dropped_overlay_ids if i],
        "coherence_kept": digest_after == digest_before,
        "structure_digest": digest_after,
    }


# ------------------------------------------------------ the choice gate


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
    return path


def read_plan_choice(workdir: Any) -> str:
    """La scelta depositata (``full``/``fast``), o ``""``.

    ``plan_choice.json`` porta due cose diverse: il segnale d'attesa scritto da
    questo modulo (``choice: null``) e la risposta umana. Solo la seconda e'
    una scelta; un file rotto o il nostro stesso segnale non sono risposte.
    """
    path = Path(str(workdir)) / PLAN_CHOICE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, Mapping):
        return ""
    value = str(data.get("choice") or "").strip().lower()
    return value if value in PLAN_CHOICE_VALUES else ""


def _estimate_line(full: Mapping[str, Any], limit: float, why: str) -> str:
    """La riga che dichiara la stima prima di partire, con la sua base."""
    source = "calibrata sui giri passati" if full.get("calibrated") else "costanti dichiarate"
    return (
        f"Stima: {float(full['low']):.0f}-{float(full['high']):.0f} s "
        f"({source}, tetto {limit:.0f} s) — {why}."
    )


def plan_gate(
    workdir: Any,
    plan: Any,
    *,
    budget: Any = BUDGET_DEFAULT,
    wait: Any = PLAN_WAIT_DEFAULT,
    preset: Any = "medium",
    crf: Any = 18,
    x264_vs_hw: Any = "x264",
    timings: Any = None,
    history: Any = None,
    gate: bool = True,
    clock: Any = None,
    sleep: Any = None,
    notify: Any = None,
) -> Dict[str, Any]:
    """Stima prima di partire; sopra il tetto chiede full o fast.

    Ordine dei casi, tutti finiti:

    1. ``estimate["high"] <= budget``: si parte col piano com'e', nessuna
       domanda e nessun file scritto (``asked=False``, ``selected_by="budget"``);
    2. sopra il tetto si scrive ``<workdir>/plan_choice.json`` come segnale
       d'attesa e si legge lo stesso file ogni secondo, al massimo ``wait``;
       una scelta gia' presente vince subito;
    3. ``{"choice":"full"}`` prosegue col piano intatto (``selected_by="user"``);
    4. ``{"choice":"fast"}`` ricostruisce la versione rapida e dichiara
       ``saved_seconds_estimate`` e ``traded``;
    5. allo scadere si prende ``fast`` e si dichiara ``selected_by="timeout"``:
       un'attesa finita non e' un blocco.

    ``clock``, ``sleep`` e ``notify`` sono iniettabili: la prova non aspetta
    davvero e non stampa.
    """
    root = Path(str(workdir))
    tick = clock if clock is not None else time.monotonic
    rest = sleep if sleep is not None else time.sleep
    say = notify if notify is not None else (lambda _msg: None)
    choice_path = root / PLAN_CHOICE_NAME

    rows = timings if timings is not None else load_timings(root)
    full = plan_estimate(
        plan, timings=rows, history=history, preset=preset, crf=crf, x264_vs_hw=x264_vs_hw
    )
    limit = float(budget) if budget is not None else BUDGET_DEFAULT
    section: Dict[str, Any] = {
        "schema": PLAN_CHOICE_SCHEMA,
        "asked": False,
        "chosen": "full",
        "budget_seconds": round(limit, 3),
        "selected_by": "budget",
        "waited_sec": 0.0,
        "saved_seconds_estimate": 0.0,
        "traded": [],
        "estimate_full": {
            "seconds": full["seconds"],
            "low": full["low"],
            "high": full["high"],
            "calibrated": full["calibrated"],
        },
    }
    if not gate:
        section["selected_by"] = "auto"
        say(_estimate_line(full, limit, "cancello della scelta chiuso: nessuna domanda"))
        return {"estimate": full, "plan_choice": section, "plan": plan}
    if float(full["high"]) <= limit:
        say(_estimate_line(full, limit, "dentro il tetto: si parte col piano completo"))
        return {"estimate": full, "plan_choice": section, "plan": plan}

    section["asked"] = True
    # Una scelta gia' depositata vince subito: non si sovrascrive con il
    # segnale d'attesa, che parlerebbe di una domanda gia' risposta.
    choice = read_plan_choice(root)
    if not choice:
        _write_json(
            choice_path,
            {
                "schema": PLAN_CHOICE_SCHEMA,
                "choice": None,
                "asked": True,
                "budget_seconds": round(limit, 3),
                "wait_sec": float(wait or 0.0),
                "estimate": {
                    "seconds": full["seconds"],
                    "low": full["low"],
                    "high": full["high"],
                    "calibrated": full["calibrated"],
                    "parts": full["parts"],
                },
                "options": {
                    "full": "il piano com'e', piu' lento",
                    "fast": "piano rapido: meno effetti, risoluzione di lavoro piu' "
                    "bassa, preset veloce; la coerenza resta la stessa",
                },
                "since_utc": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            },
        )
        say(
            "Stima: "
            f"{full['low']:.0f}-{full['high']:.0f} s sopra il tetto di {limit:.0f} s. "
            f"Scegli full o fast in {choice_path} entro {float(wait or 0.0):.0f} s, "
            "altrimenti si parte con la versione rapida."
        )

    started = tick()
    waited = 0.0
    if not choice and float(wait or 0.0) > 0:
        deadline = started + float(wait)
        while True:
            remaining = deadline - tick()
            if remaining <= 0:
                break
            rest(min(1.0, remaining))
            choice = read_plan_choice(root)
            if choice:
                break
        waited = max(0.0, tick() - started)
    section["waited_sec"] = round(waited, 3)

    if choice == "full":
        section.update({"chosen": "full", "selected_by": "user"})
        say("Piano: versione completa scelta nella workdir.")
        return {"estimate": full, "plan_choice": section, "plan": plan}

    fast = fast_plan(plan, preset=FAST_PRESET)
    fast_est = plan_estimate(
        fast["plan"],
        timings=rows,
        history=history,
        preset=fast["preset"],
        crf=crf,
        x264_vs_hw=x264_vs_hw,
    )
    section.update(
        {
            "chosen": "fast",
            "selected_by": "user" if choice == "fast" else "timeout",
            "saved_seconds_estimate": round(full["seconds"] - fast_est["seconds"], 3),
            "traded": list(fast["traded"]),
            "fast_preset": fast["preset"],
            "fast_resolution": list(fast["resolution"]),
            "coherence_kept": bool(fast["coherence_kept"]),
        }
    )
    if not choice:
        # Nessuno ha risposto: un segnale d'attesa rimasto direbbe il falso.
        choice_path.unlink(missing_ok=True)
        say("Piano: nessuna scelta in tempo, si parte con la versione rapida.")
    else:
        say("Piano: versione rapida scelta nella workdir.")
    return {"estimate": fast_est, "plan_choice": section, "plan": fast["plan"]}


# ------------------------------------------------- recording the timings


def record_timings(
    workdir: Any,
    report: Mapping[str, Any],
    *,
    history_path: Any = None,
) -> Optional[Dict[str, Any]]:
    """Appende i tempi misurati di un giro: la prossima stima si calibra qui.

    Il profilo di ``auto`` e' gia' la misura (``report["timings"]``); la riga
    aggiunge le unita' di lavoro senza cui i secondi non si normalizzano. Un
    report senza ``timings`` non scrive niente: meglio nessuna riga che una
    riga finta.
    """
    if not isinstance(report, Mapping):
        return None
    timings = report.get("timings")
    if not isinstance(timings, Mapping) or not timings:
        return None
    info = report.get("estimate") if isinstance(report.get("estimate"), Mapping) else {}
    job = info.get("job") if isinstance(info.get("job"), Mapping) else {}
    resolution = info.get("resolution") or job.get("resolution") or [0, 0]
    try:
        width = int(job.get("width") or (resolution[0] if len(resolution) > 0 else 0))
        height = int(job.get("height") or (resolution[1] if len(resolution) > 1 else 0))
    except (TypeError, ValueError, IndexError):
        width, height = 0, 0
    row = {
        "timeline_sec": round(float(report.get("timeline_sec") or 0.0), 3),
        "material_sec": round(
            float(info.get("material_sec") or report.get("timeline_sec") or 0.0), 3
        ),
        "width": width,
        "height": height,
        "fps": float(report.get("fps") or 0.0),
        "preset": str(info.get("preset") or job.get("preset") or "medium"),
        "x264_vs_hw": _codec_class(info.get("x264_vs_hw") or job.get("x264_vs_hw")),
        "effect_cost": (info.get("effects") or {}).get("cost"),
        "seconds_total": round(float(timings.get("total") or 0.0), 3),
        "timings": {
            stage: round(float(timings[stage]), 3)
            for stage in (*ESTIMATE_STAGES, "deliverables")
            if isinstance(timings.get(stage), (int, float))
        },
    }
    target = (
        Path(str(history_path))
        if history_path
        else default_timing_path(Path(str(workdir)))
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return row
