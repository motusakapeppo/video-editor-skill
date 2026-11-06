"""vx.verify — le falsificazioni del registro, eseguite invece che raccontate.

``vx.decisions`` scrive per ogni voce cosa la smentirebbe. Una frase cosi' e'
solo una promessa finche' nessuno la misura: questo modulo e' il registro delle
verifiche deterministiche che la mantengono.

Ogni check legge il master (o il report) e restituisce::

    {"id": "contrast", "passed": False,
     "measured": 1.3, "expected": 4.5,
     "why": "contrasto WCAG del titolo sotto soglia"}

I check disponibili, tutti senza rete:

================  =====================================================  =========
id                cosa misura                                           soglia
================  =====================================================  =========
``contrast``      rapporto WCAG testo/sfondo nella banda del titolo     >= 4.5
``duration``      durata del master contro quella richiesta             +-1 frame
``black_frames``  luma media dei fotogrammi campionati a 1 fps          >= 0.02
``loudness``      LUFS integrato e true peak contro il target dichiarato  +-1.5 LU
``variety``       shot distinti / shot totali dal referto del piano     >= 0.5
``text_present``  titolo/hook o cue presenti quando sono richiesti       >= 1
================  =====================================================  =========

``verify_all(report, master)`` esegue tutti i check, scrive l'esito in
``report["verification"]`` e annota ogni voce di ``report["decisions"]`` con
``verified`` e ``verification``. Una voce senza un check automatico resta
``verified: false`` con ``why: "falsifier non automatizzabile"``: il modulo non
trasforma la prosa in un verde.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import ffmpeg as ff

__all__ = [
    "BLACK_LUMA",
    "CHECKS",
    "CHECK_ORDER",
    "CONTRAST_MIN",
    "DECISION_CHECKS",
    "DURATION_TOLERANCE_FRAMES",
    "LOUDNESS_TOLERANCE_LU",
    "MIN_VARIETY",
    "TRUE_PEAK_MARGIN_DB",
    "annotate_decisions",
    "check_black_frames",
    "check_contrast",
    "check_duration",
    "check_loudness",
    "check_text_present",
    "check_variety",
    "verify_all",
    "wcag_ratio",
]

CONTRAST_MIN = 4.5
BLACK_LUMA = 0.02
BLACK_SAMPLE_FPS = 1.0
DURATION_TOLERANCE_FRAMES = 1.0
LOUDNESS_TOLERANCE_LU = 1.5
TRUE_PEAK_MARGIN_DB = 0.1
MIN_VARIETY = 0.5

#: Quale check smentisce quale decisione. Solo dove il nesso e' letterale:
#: ``font`` -> leggibilita' del titolo, ``plan`` -> durata consegnata,
#: ``coherence`` -> varieta' degli shot. Le altre voci restano non verificate
#: invece di prendersi un check che non le riguarda.
DECISION_CHECKS: Dict[str, str] = {
    "font": "contrast",
    "plan": "duration",
    "coherence": "variety",
}

CHECK_ORDER = (
    "contrast",
    "duration",
    "black_frames",
    "loudness",
    "variety",
    "text_present",
)

#: Lato del campione quadrato su cui si misura il contrasto. Piccolo abbastanza
#: da non pesare, grande abbastanza da conservare i bordi del testo.
_CONTRAST_SIDE = 160
#: Lato del campione per la luma dei black frame: la media non ha bisogno di
#: dettaglio, e 32x32 rende un secondo di video meno di un kilobyte per frame.
_BLACK_SIDE = 32
_DEFAULT_REGION = (0.0, 0.0, 1.0, 0.35)


def _result(
    check_id: str,
    passed: bool,
    measured: Any,
    expected: Any,
    why: str = "",
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "passed": bool(passed),
        "measured": measured,
        "expected": expected,
        "why": why,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


# --------------------------------------------------------------------------- #
# lettura del master
# --------------------------------------------------------------------------- #


def _run_bytes(args: Sequence[Any], *, timeout: int = 180) -> bytes:
    """ffmpeg -> stdout binario.

    ``ffmpeg.run_ff`` decodifica lo stdout come testo e corromperebbe i frame
    raw; qui il processo parla la stessa lingua del file. Nessuna shell, nessun
    file temporaneo.
    """
    argv = [ff.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error"]
    argv += [str(item) for item in args]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=creationflags,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg: {(tail[-1] if tail else 'errore')[:240]}")
    return proc.stdout or b""


def _gray_frames(master: Any, filtergraph: str, *, extra: Sequence[Any] = ()) -> bytes:
    return _run_bytes(
        [
            "-i",
            str(master),
            *extra,
            "-vf",
            filtergraph,
            "-an",
            "-sn",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ]
    )


def _probe_value(master: Any, entries: str, stream: bool = False) -> str:
    args: List[Any] = ["-v", "error"]
    if stream:
        args += ["-select_streams", "v:0"]
    args += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(master)]
    proc = ff.run_fp(args)
    if not proc.ok:
        return ""
    return (proc.stdout or "").strip().splitlines()[0].strip() if proc.stdout else ""


def _duration_of(master: Any) -> Optional[float]:
    value = _probe_value(master, "format=duration")
    return _num(value)


def _fps_of(master: Any) -> Optional[float]:
    value = _probe_value(master, "stream=avg_frame_rate", stream=True)
    if not value:
        return None
    if "/" in value:
        left, right = value.split("/", 1)
        num, den = _num(left), _num(right)
        if num and den:
            return num / den
        return None
    return _num(value)


def _loudness_of(master: Any) -> Dict[str, float]:
    """LUFS integrato e true peak dalla misura loudnorm, sul file consegnato."""
    proc = ff.run_ff(
        ["-i", str(master), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        loglevel="info",
    )
    if not proc.ok:
        return {}
    text = proc.stderr or ""
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    out: Dict[str, float] = {}
    for key, value in raw.items():
        number = _num(value)
        if number is not None:
            out[key] = number
    return out


# --------------------------------------------------------------------------- #
# misure pure
# --------------------------------------------------------------------------- #


def _srgb_linear(value: float) -> float:
    c = max(0.0, min(255.0, float(value))) / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def wcag_ratio(light: float, dark: float) -> float:
    """Rapporto di contrasto WCAG fra due luma 0..255."""
    a = _srgb_linear(light)
    b = _srgb_linear(dark)
    hi, lo = (a, b) if a >= b else (b, a)
    return (hi + 0.05) / (lo + 0.05)


def _percentile(values: Sequence[int], percent: float) -> float:
    if not values:
        raise ValueError("nessun pixel nella regione")
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * max(0.0, min(100.0, percent)) / 100.0))
    return float(ordered[index])


def _region_values(
    frame: bytes, side: int, region: Sequence[float]
) -> List[int]:
    if len(frame) < side * side:
        raise ValueError("fotogramma incompleto")
    x, y, w, h = (float(item) for item in region)
    x0 = max(0, min(side - 1, int(round(x * side))))
    y0 = max(0, min(side - 1, int(round(y * side))))
    x1 = max(x0 + 1, min(side, int(round((x + w) * side))))
    y1 = max(y0 + 1, min(side, int(round((y + h) * side))))
    return [
        frame[row * side + col]
        for row in range(y0, y1)
        for col in range(x0, x1)
    ]


# --------------------------------------------------------------------------- #
# i check
# --------------------------------------------------------------------------- #


def check_contrast(master: Any, report: Mapping[str, Any]) -> Dict[str, Any]:
    """Contrasto WCAG del testo del titolo/hook su un fotogramma campionato.

    La banda del titolo e' ``report["contrast_region"]`` (frazioni ``x,y,w,h``;
    default la fascia alta). Il contrasto e' fra il 95mo e il 5o percentile di
    luma: se nella banda non c'e' testo, i due valori coincidono e il check
    boccia, che e' la risposta onesta — non si puo' dimostrare leggibile un
    titolo che non c'e'.
    """
    region = report.get("contrast_region")
    if not _text_required(report):
        # Senza titolo non c'e' un contrasto da garantire: misurare il fondo e
        # bocciare un master che non porta testo sarebbe un falso negativo.
        return _result(
            "contrast",
            True,
            {"applicable": False},
            {"min_ratio": CONTRAST_MIN},
            "nessun titolo richiesto: contrasto non applicabile",
        )
    if not (
        isinstance(region, (list, tuple)) and len(region) == 4
    ):
        region = _DEFAULT_REGION
    at = _num(report.get("contrast_at"))
    if at is None:
        at = 1.0
    try:
        frame = _run_bytes(
            [
                "-ss",
                f"{max(0.0, at):.3f}",
                "-i",
                str(master),
                "-frames:v",
                "1",
                "-vf",
                f"scale={_CONTRAST_SIDE}:{_CONTRAST_SIDE}",
                "-an",
                "-sn",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ]
        )
        values = _region_values(frame, _CONTRAST_SIDE, region)
        dark = _percentile(values, 5)
        light = _percentile(values, 95)
    except Exception as exc:  # noqa: BLE001 - un check rotto e' un check fallito
        return _result(
            "contrast",
            False,
            None,
            {"min_ratio": CONTRAST_MIN, "at_sec": at, "region": list(region)},
            f"misura non eseguita: {type(exc).__name__}: {exc}",
        )
    ratio = round(wcag_ratio(light, dark), 2)
    return _result(
        "contrast",
        ratio >= CONTRAST_MIN,
        {"ratio": ratio, "dark": round(dark, 1), "light": round(light, 1)},
        {"min_ratio": CONTRAST_MIN, "at_sec": at, "region": list(region)},
        (
            f"contrasto {ratio:.2f} >= {CONTRAST_MIN:.1f}"
            if ratio >= CONTRAST_MIN
            else f"contrasto {ratio:.2f} < {CONTRAST_MIN:.1f}: titolo illeggibile"
        ),
    )


def _expected_duration(report: Mapping[str, Any]) -> Optional[float]:
    for key in (
        "duration_requested_sec",
        "requested_duration_sec",
        "expected_duration_sec",
        "duration_sec",
        "timeline_sec",
    ):
        value = _num(report.get(key))
        if value is not None and value > 0:
            return value
    scene = _mapping(report.get("scene"))
    value = _num(scene.get("duration_sec"))
    return value if value is not None and value > 0 else None


def check_duration(master: Any, report: Mapping[str, Any]) -> Dict[str, Any]:
    """Durata del master entro un frame dalla durata richiesta."""
    expected = _expected_duration(report)
    if expected is None:
        return _result(
            "duration",
            False,
            None,
            {"tolerance_frames": DURATION_TOLERANCE_FRAMES},
            "durata richiesta non dichiarata nel report",
        )
    fps = _num(report.get("fps")) or _fps_of(master)
    if not fps or fps <= 0:
        return _result(
            "duration",
            False,
            None,
            {"requested_sec": expected, "tolerance_frames": DURATION_TOLERANCE_FRAMES},
            "frame rate non dichiarato ne' misurabile",
        )
    tolerance = DURATION_TOLERANCE_FRAMES / float(fps)
    actual = _duration_of(master)
    if actual is None or actual <= 0:
        return _result(
            "duration",
            False,
            None,
            {
                "requested_sec": expected,
                "tolerance_frames": DURATION_TOLERANCE_FRAMES,
                "tolerance_sec": round(tolerance, 4),
                "fps": round(float(fps), 3),
            },
            "durata del master non misurabile",
        )
    delta = abs(actual - expected)
    return _result(
        "duration",
        delta <= tolerance + 1e-6,
        {
            "duration_sec": round(actual, 3),
            "delta_sec": round(delta, 3),
            "tolerance_sec": round(tolerance, 4),
        },
        {
            "requested_sec": round(expected, 3),
            "tolerance_frames": DURATION_TOLERANCE_FRAMES,
            "tolerance_sec": round(tolerance, 4),
            "fps": round(float(fps), 3),
        },
        (
            f"durata {actual:.3f}s entro {tolerance:.3f}s da {expected:.3f}s"
            if delta <= tolerance + 1e-6
            else f"durata {actual:.3f}s fuori di {delta:.3f}s da {expected:.3f}s"
        ),
    )


def check_black_frames(master: Any, report: Mapping[str, Any]) -> Dict[str, Any]:
    """Nessun frame con luma media sotto 0.02, campionando a 1 fps."""
    try:
        raw = _gray_frames(
            master,
            f"fps={BLACK_SAMPLE_FPS:g},scale={_BLACK_SIDE}:{_BLACK_SIDE}",
        )
    except Exception as exc:  # noqa: BLE001
        return _result(
            "black_frames",
            False,
            None,
            {"black_frames": 0, "luma_min": BLACK_LUMA},
            f"misura non eseguita: {type(exc).__name__}: {exc}",
        )
    size = _BLACK_SIDE * _BLACK_SIDE
    frames = len(raw) // size
    if frames <= 0:
        return _result(
            "black_frames",
            False,
            None,
            {"black_frames": 0, "luma_min": BLACK_LUMA},
            "nessun fotogramma decodificato",
        )
    lumas = []
    for index in range(frames):
        chunk = raw[index * size : (index + 1) * size]
        lumas.append(sum(chunk) / float(size) / 255.0)
    black = [round(value, 4) for value in lumas if value < BLACK_LUMA]
    return _result(
        "black_frames",
        not black,
        {
            "frames": frames,
            "min_luma": round(min(lumas), 4),
            "black_frames": len(black),
            "black_lumas": black[:10],
        },
        {"black_frames": 0, "luma_min": BLACK_LUMA, "sample_fps": BLACK_SAMPLE_FPS},
        (
            f"{frames} fotogrammi, minimo {min(lumas):.4f}"
            if not black
            else f"{len(black)} fotogrammi sotto {BLACK_LUMA:.2f} "
            f"(minimo {min(lumas):.4f})"
        ),
    )


def _loudness_targets(report: Mapping[str, Any]) -> Tuple[float, float, float]:
    raw = report.get("loudness_target") or report.get("target_loudness") or {}
    target_i = target_tp = None
    if isinstance(raw, Mapping):
        target_i = _num(raw.get("lufs", raw.get("i", raw.get("I"))))
        target_tp = _num(raw.get("true_peak_db", raw.get("tp", raw.get("TP"))))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        target_i, target_tp = _num(raw[0]), _num(raw[1])
    tolerance = _num(report.get("loudness_tolerance_lu"))
    if tolerance is None or tolerance <= 0:
        tolerance = LOUDNESS_TOLERANCE_LU
    return (
        -14.0 if target_i is None else target_i,
        -1.0 if target_tp is None else target_tp,
        tolerance,
    )


def check_loudness(master: Any, report: Mapping[str, Any]) -> Dict[str, Any]:
    """LUFS integrato e true peak dentro le tolleranze dichiarate."""
    target_i, target_tp, tolerance = _loudness_targets(report)
    expected = {
        "lufs": target_i,
        "tolerance_lu": tolerance,
        "true_peak_db": target_tp,
        "true_peak_margin_db": TRUE_PEAK_MARGIN_DB,
    }
    measured = _loudness_of(master)
    lufs = _num(measured.get("input_i"))
    peak = _num(measured.get("input_tp"))
    if lufs is None or peak is None:
        return _result(
            "loudness",
            False,
            {"lufs": lufs, "true_peak_db": peak},
            expected,
            "nessuna misura loudness: audio assente o illeggibile",
        )
    delta = abs(lufs - target_i)
    peak_ok = peak <= target_tp + TRUE_PEAK_MARGIN_DB
    passed = delta <= tolerance and peak_ok
    reasons = []
    if delta > tolerance:
        reasons.append(f"LUFS {lufs:.2f} fuori di {delta:.2f} da {target_i:.2f}")
    if not peak_ok:
        reasons.append(
            f"true peak {peak:.2f} oltre {target_tp + TRUE_PEAK_MARGIN_DB:.2f} dBTP"
        )
    return _result(
        "loudness",
        passed,
        {
            "lufs": round(lufs, 2),
            "delta_lu": round(delta, 2),
            "true_peak_db": round(peak, 2),
        },
        expected,
        "; ".join(reasons) if reasons else f"LUFS {lufs:.2f}, TP {peak:.2f}",
    )


def _variety_values(report: Mapping[str, Any]) -> Tuple[Optional[float], Any]:
    coherence = _mapping(report.get("coherence"))
    metrics = _mapping(coherence.get("metrics"))
    variety = _num(metrics.get("variety"))
    if variety is None:
        variety = _num(report.get("variety"))
    if variety is None:
        stats = _mapping(report.get("plan_stats"))
        variety = _num(stats.get("variety"))
        if variety is None:
            distinct = _num(stats.get("distinct_shots"))
            shots = _num(stats.get("shots"))
            if distinct is not None and shots:
                variety = distinct / shots
    detail = {
        "distinct_shots": metrics.get("distinct_shots") or _mapping(
            report.get("plan_stats")
        ).get("distinct_shots"),
        "shots": metrics.get("shots") or _mapping(report.get("plan_stats")).get("shots"),
    }
    threshold = _num(report.get("min_variety"))
    if threshold is None:
        threshold = _num(_mapping(metrics.get("thresholds")).get("min_variety"))
    return variety, {"detail": detail, "min_variety": MIN_VARIETY if threshold is None else threshold}


def check_variety(master: Any, report: Mapping[str, Any]) -> Dict[str, Any]:
    """Rapporto shot distinti/totali misurato dal referto del piano."""
    variety, extra = _variety_values(report)
    threshold = float(extra["min_variety"])
    expected = {"min_variety": threshold, "detail": extra["detail"]}
    if variety is None:
        # "Non misurabile" non e' "insufficiente". Prima questo ramo bloccava la
        # consegna: un reel a inquadratura singola (o un piano che non dichiara
        # gli shot distinti) finiva NON-PUBBLICABILE per un check che non aveva
        # niente da misurare. Le ripetizioni vere le misura la legge B
        # (coherence, pHash) e le toglie; qui si dichiara e si prosegue.
        return _result(
            "variety",
            True,
            {"applicable": False},
            expected,
            "varieta' non misurabile su questo piano: il referto non porta le "
            "inquadrature distinte; le ripetizioni le misura la legge B "
            "(coherence), non questo check",
        )
    return _result(
        "variety",
        variety >= threshold,
        {"variety": round(variety, 3), "detail": extra["detail"]},
        expected,
        (
            f"varieta' {variety:.2f} >= {threshold:.2f}"
            if variety >= threshold
            else f"varieta' {variety:.2f} < {threshold:.2f}: shot ripetuti"
        ),
    )


def _text_paths(report: Mapping[str, Any]) -> List[Path]:
    raw: List[Any] = []
    for key in ("subtitles", "captions", "ass", "srt"):
        raw.append(report.get(key))
    raw.append(_mapping(report.get("bridge")).get("subtitles"))
    deliverables = report.get("deliverables")
    if isinstance(deliverables, (list, tuple)):
        for item in deliverables:
            if isinstance(item, Mapping):
                kind = str(item.get("kind") or "").lower()
                path = item.get("path") or item.get("file")
                if path and (kind in ("srt", "ass", "vtt") or str(path).endswith((".srt", ".ass", ".vtt"))):
                    raw.append(path)
            elif isinstance(item, str):
                raw.append(item)
    paths: List[Path] = []
    for item in raw:
        if not item:
            continue
        path = Path(str(item))
        if path.suffix.lower() in (".srt", ".ass", ".vtt") and path not in paths:
            paths.append(path)
    return paths


def _cue_count(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return 0
    if path.suffix.lower() == ".ass":
        return sum(1 for line in text.splitlines() if line.lower().startswith("dialogue:"))
    return sum(1 for line in text.splitlines() if "-->" in line)


def _text_blocks(report: Mapping[str, Any]) -> List[str]:
    blocks: List[str] = []
    for key in ("texts", "text", "titles", "title", "hook"):
        value = report.get(key)
        if isinstance(value, str) and value.strip():
            blocks.append(value.strip())
        elif isinstance(value, Mapping):
            blocks.extend(str(item).strip() for item in value.values() if str(item).strip())
        elif isinstance(value, (list, tuple)):
            blocks.extend(str(item).strip() for item in value if str(item).strip())
    for path in _text_paths(report):
        if _cue_count(path) > 0:
            blocks.append(str(path))
    return blocks


def _text_required(report: Mapping[str, Any]) -> bool:
    for key in ("text_required", "requires_text"):
        if key in report:
            return bool(report[key])
    requirements = _mapping(report.get("verification_requirements"))
    if "text_present" in requirements:
        return bool(requirements["text_present"])
    return bool(_text_blocks(report))


def check_text_present(master: Any, report: Mapping[str, Any]) -> Dict[str, Any]:
    """Titolo/hook o cue presenti quando il report li dichiara richiesti."""
    required = _text_required(report)
    blocks = _text_blocks(report)
    expected = {"required": required, "min_blocks": 1 if required else 0}
    measured = {"required": required, "text_blocks": len(blocks)}
    if not required:
        return _result(
            "text_present",
            True,
            measured,
            expected,
            "testo non richiesto per questo lavoro",
        )
    return _result(
        "text_present",
        bool(blocks),
        measured,
        expected,
        (
            f"{len(blocks)} blocchi di testo nel report/consegna"
            if blocks
            else "titolo/hook richiesto ma nessun testo nel report o nella consegna"
        ),
    )


CHECKS = {
    "contrast": check_contrast,
    "duration": check_duration,
    "black_frames": check_black_frames,
    "loudness": check_loudness,
    "variety": check_variety,
    "text_present": check_text_present,
}


# --------------------------------------------------------------------------- #
# il verdetto sul registro
# --------------------------------------------------------------------------- #


def annotate_decisions(
    decisions: Iterable[Mapping[str, Any]], result: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Scrive ``verified``/``verification`` sulle voci, senza promuoverle a prova.

    Il check puo' essere dichiarato dalla voce (``check``) o dedotto dal suo id
    (``DECISION_CHECKS``). Se non esiste, o non e' stato eseguito, la voce resta
    ``verified: false`` e dice perche'.
    """
    by_id = {
        str(item.get("id")): item
        for item in result.get("checks") or ()
        if isinstance(item, Mapping)
    }
    annotated: List[Dict[str, Any]] = []
    for raw in decisions or ():
        if not isinstance(raw, Mapping):
            continue
        entry = dict(raw)
        check_id = str(entry.get("check") or DECISION_CHECKS.get(str(entry.get("id"))) or "").strip()
        check = by_id.get(check_id) if check_id else None
        if check_id:
            entry["check"] = check_id
        if check is None:
            entry["verified"] = False
            entry["verification"] = {
                "check": check_id or None,
                "why": (
                    "check non eseguito"
                    if check_id
                    else "falsifier non automatizzabile"
                ),
            }
        else:
            entry["verified"] = bool(check.get("passed"))
            entry["verification"] = {
                "check": check_id,
                "passed": bool(check.get("passed")),
                "measured": check.get("measured"),
                "expected": check.get("expected"),
                "why": str(check.get("why") or ""),
            }
        annotated.append(entry)
    return annotated


def verify_all(
    report: Any,
    master: Any,
    *,
    checks: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Esegue i check, annota le decisioni e scrive ``report["verification"]``.

    Un check che solleva e' un check fallito, mai un'eccezione che ferma il
    report: il verdetto e' un valore, come nel resto della skill. ``report``
    viene mutato quando e' un dizionario; il risultato e' comunque restituito.
    """
    payload = report if isinstance(report, dict) else {}
    master_path = master
    results: List[Dict[str, Any]] = []
    degraded: List[str] = []
    for check_id in checks or CHECK_ORDER:
        fn = CHECKS.get(str(check_id))
        if fn is None:
            results.append(
                _result(str(check_id), False, None, None, "check sconosciuto")
            )
            continue
        try:
            results.append(fn(master_path, payload))
        except Exception as exc:  # noqa: BLE001 - un check rotto e' un fallito
            degraded.append(str(check_id))
            results.append(
                _result(
                    str(check_id),
                    False,
                    None,
                    None,
                    f"check non eseguito: {type(exc).__name__}: {exc}",
                )
            )
    verdict: Dict[str, Any] = {"ok": all(item["passed"] for item in results), "checks": results}
    if degraded:
        # Un check che non e' riuscito a misurare non e' la stessa cosa di un
        # check che ha misurato male: il report lo dichiara a parte, cosi'
        # ``degraded`` non diventa un sinonimo di "bocciato".
        verdict["degraded"] = degraded
    if isinstance(report, dict):
        report["verification"] = verdict
        if isinstance(report.get("decisions"), list):
            report["decisions"] = annotate_decisions(report["decisions"], verdict)
    return verdict
