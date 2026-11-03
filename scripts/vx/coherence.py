"""vx.coherence — il referto che dice se un montaggio ha un senso.

Un montaggio non e' una sequenza di fotogrammi qualsiasi: deve variare, non
ripetersi, non mostrare frame inutilizzabili, aprire con un motivo e dire perche'
ogni taglio esiste. Questo modulo guarda il piano gia' costruito (``plan.json``)
e il referto che l'ha prodotto (``auto_report.json`` / ``bridge_report.json``) e
restituisce:

    {"ok": bool, "violations": [...], "metrics": {...}}

Cinque controlli, ognuno con la sua prova:

* ``low_variety``      — shot distinti / shot totali sotto :data:`MIN_VARIETY`;
* ``repeated_shots``   — >= :data:`REPEAT_RUN` shot quasi identici di fila;
* ``unusable_frame``   — frame nero (luma < :data:`BLACK_LUMA`) o mosso/uniforme
  oltre la soglia di nitidezza gia' usata dal triage;
* ``weak_open``        — il primo shot non ha ne' un hook ne' il primo verso;
* ``unjustified_cut``  — tagli senza provenienza beat/scene/speech/chorus.

La regola dell'onesta': un dato che manca non e' una violazione. Se i fotogrammi
non si possono leggere, se la nitidezza non e' misurabile o se il piano non porta
nessuna provenienza (i piani vecchi), il controllo finisce in ``metrics.unknown``
con il motivo, e ``ok`` non peggiora per un dato che nessuno ha misurato.

Il modulo non disegna, non rende e non scrive: legge file gia' esistenti o
fotogrammi passati dal chiamante. Per questo i test possono costruire piani
sintetici e PNG senza ffmpeg e senza rete.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contract import THRESHOLDS

__all__ = [
    "BLACK_LUMA",
    "CUT_SOURCES",
    "MIN_VARIETY",
    "REPEAT_RUN",
    "SHOT_SAME_DISTANCE",
    "disabled",
    "evaluate",
    "sample_frames",
]

#: Due fotogrammi con distanza media sotto questa soglia sono lo stesso shot.
#: Piu' larga della soglia dei duplicati del triage (0.06) perche' qui si
#: confrontano due punti qualsiasi di due inquadrature, non tre punti fissi
#: dello stesso take.
SHOT_SAME_DISTANCE = 0.12

#: Sotto questo rapporto di shot distinti il montaggio non varia abbastanza.
MIN_VARIETY = 0.5

#: Tre shot quasi identici di fila sono una ripetizione, non una scelta.
REPEAT_RUN = 3

#: Un frame con luma media sotto questo valore e' nero: non mostra niente.
BLACK_LUMA = 0.02

#: Le provenienze che giustificano un taglio. Un taglio senza una di queste,
#: quando il piano dichiara provenienze, e' ``unjustified_cut``.
CUT_SOURCES = ("beat", "scene", "speech", "chorus")

#: Tolleranza (secondi) fra un taglio e l'ancora che lo giustifica.
CUT_TOLERANCE = 0.15
#: La stessa tolleranza per i confini di parola: un taglio dentro la parola non
#: e' un taglio sul parlato.
WORD_TOLERANCE = 0.10

#: Gli ``anchor.kind`` del ponte che valgono come provenienza di un taglio.
_ANCHOR_SOURCES = {
    "beat": "beat",
    "bar": "beat",
    "shot": "scene",
    "scene": "scene",
    "word_span": "speech",
    "transcript_span": "speech",
    "keyword": "speech",
    "silence_span": "speech",
    "music_section": "chorus",
    "chorus": "chorus",
}

_PHASH_SIDE = 8


# --------------------------------------------------------------------------- #
# lettura del piano e del referto
# --------------------------------------------------------------------------- #


def _as_dict(value: Any) -> Dict[str, Any]:
    """Un mapping, o il ``to_dict()`` di un oggetto; altrimenti vuoto."""
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
        except Exception:  # noqa: BLE001 - un oggetto rotto non ferma il referto
            return {}
        return dict(data) if isinstance(data, Mapping) else {}
    return {}


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _provenance_records(raw: Any) -> Tuple[Dict[str, Any], ...]:
    """Provenienze di un clip: liste di record, o una singola stringa."""
    if isinstance(raw, Mapping):
        return (dict(raw),)
    if isinstance(raw, str):
        return ({"kind": raw},)
    if isinstance(raw, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for item in raw:
            if isinstance(item, Mapping):
                out.append(dict(item))
            elif isinstance(item, str):
                out.append({"kind": item})
        return tuple(out)
    return ()


def _shots(plan: Any) -> List[Dict[str, Any]]:
    """I clip della traccia base in ordine di timeline.

    Solo il layer 0 e' il montaggio: un overlay non e' un'inquadratura e non
    deve contare nella varieta' ne' nei tagli.
    """
    data = _as_dict(plan)
    tracks = data.get("tracks")
    if isinstance(tracks, Mapping):
        video: Iterable[Any] = tracks.get("video") or ()
    else:
        video = data.get("video_tracks") or ()
    if not video:
        video = getattr(plan, "video_tracks", ()) or ()

    clips: List[Dict[str, Any]] = []
    for track in video:
        for clip in _field(track, "clips", ()) or ():
            layer = _number(_field(clip, "layer", 0)) or 0.0
            if int(layer) != 0:
                continue
            src_in = _number(_field(clip, "src_in")) or 0.0
            src_out = _number(_field(clip, "src_out")) or 0.0
            dst_in = _number(_field(clip, "dst_in")) or 0.0
            dst_out = _number(_field(clip, "dst_out")) or (dst_in + max(0.0, src_out - src_in))
            if dst_out <= dst_in:
                continue
            records = _provenance_records(
                _field(clip, "provenance")
                or _field(clip, "cut_provenance")
                or _field(clip, "cut_reason")
                or _field(clip, "reason")
            )
            clips.append(
                {
                    "clip_id": str(_field(clip, "clip_id", "") or ""),
                    "source_id": str(_field(clip, "source_id", "") or ""),
                    "src_in": src_in,
                    "src_out": src_out,
                    "dst_in": dst_in,
                    "dst_out": dst_out,
                    "provenance": records,
                }
            )
    clips.sort(key=lambda item: (item["dst_in"], item["clip_id"]))
    for index, shot in enumerate(clips):
        shot["index"] = index
        shot["duration"] = shot["dst_out"] - shot["dst_in"]
    return clips


def _visual_shots(clips: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Le inquadrature vere, non i frammenti che il ponte ha lasciato.

    Due clip contigui dello stesso sorgente (``src_out`` dell'uno = ``src_in``
    dell'altro) sono la stessa inquadratura spezzata su un silenzio: contarle
    come due shot direbbe che il montaggio ripete, mentre la camera non si e'
    mossa. La provenienza di tutti i frammenti resta sul shot fuso.
    """
    shots: List[Dict[str, Any]] = []
    for clip in clips:
        if shots:
            last = shots[-1]
            same_source = last["source_id"] == clip["source_id"]
            contiguous = (
                abs(last["src_out"] - clip["src_in"]) <= 0.02
                and abs(last["dst_out"] - clip["dst_in"]) <= 0.02
            )
            if same_source and contiguous:
                last["src_out"] = clip["src_out"]
                last["dst_out"] = clip["dst_out"]
                last["duration"] = last["dst_out"] - last["dst_in"]
                last["provenance"] = tuple(last["provenance"]) + tuple(
                    clip["provenance"]
                )
                continue
        shots.append(dict(clip, provenance=tuple(clip["provenance"])))
    for index, shot in enumerate(shots):
        shot["index"] = index
    return shots


def _bridge_dict(report: Any, bridge: Any) -> Dict[str, Any]:
    if bridge is not None:
        return _as_dict(bridge)
    if not isinstance(report, Mapping):
        report = _as_dict(report)
    embedded = report.get("bridge_report")
    if isinstance(embedded, Mapping):
        return dict(embedded)
    return {}


def _sources(plan: Any) -> Dict[str, Any]:
    data = _as_dict(plan)
    sources = data.get("sources")
    if isinstance(sources, Mapping):
        return dict(sources)
    raw = getattr(plan, "sources", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


# --------------------------------------------------------------------------- #
# fotogrammi: lettura, impronta, luma, nitidezza
# --------------------------------------------------------------------------- #


def _gray(path: Any) -> Any:
    """La matrice di grigi 0..255 del PNG/JPEG, o un errore dichiarato."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001 - senza OpenCV si prova con Pillow
        try:
            from PIL import Image  # type: ignore
            import numpy as np  # type: ignore

            with Image.open(str(path)) as image:
                return np.asarray(image.convert("L"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"fotogramma non leggibile: {exc}") from exc
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError("fotogramma non decodificabile")
    return image


def _resize(gray: Any, width: int, height: int) -> Any:
    try:
        import cv2  # type: ignore

        return cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    except Exception:  # noqa: BLE001
        from PIL import Image  # type: ignore
        import numpy as np  # type: ignore

        return np.asarray(
            Image.fromarray(gray).resize((width, height), Image.Resampling.BILINEAR)
        )


def _fingerprint(gray: Any) -> str:
    """dHash 8x8: ``left > right`` su una miniatura 9x8."""
    thumb = _resize(gray, _PHASH_SIDE + 1, _PHASH_SIDE)
    bits: List[str] = []
    for row in range(_PHASH_SIDE):
        for col in range(_PHASH_SIDE):
            bits.append("1" if thumb[row][col] > thumb[row][col + 1] else "0")
    value = int("".join(bits), 2)
    return f"{value:0{_PHASH_SIDE * _PHASH_SIDE // 4}x}"


def _distance(left: str, right: str) -> Optional[float]:
    if not left or not right or len(left) != len(right):
        return None
    try:
        differing = sum(
            bin(int(a, 16) ^ int(b, 16)).count("1") for a, b in zip(left, right)
        )
    except ValueError:
        return None
    return differing / float(len(left) * 4)


def _sharpness(gray: Any) -> Optional[float]:
    """Varianza del laplaciano, la stessa misura del triage; ``None`` se assente."""
    try:
        import cv2  # type: ignore

        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:  # noqa: BLE001 - senza OpenCV il dato non esiste
        return None


def _frame_path(frames: Any, index: int, clip_id: str) -> Any:
    if frames is None:
        return None
    if isinstance(frames, Mapping):
        for key in (index, str(index), clip_id):
            if key in frames:
                return frames[key]
        return None
    if isinstance(frames, (list, tuple)):
        return frames[index] if index < len(frames) else None
    return None


def sample_frames(plan: Any, workdir: Any) -> Dict[int, str]:
    """Un PNG per shot, al centro del segmento sorgente.

    Il campionamento serve a guardare l'inquadratura che il piano usa davvero,
    non il primo fotogramma del file. Un'estrazione fallita non compare nel
    dizionario: il controllo dira' ``unknown`` invece di inventare un frame.
    """
    from .ffmpeg import ffmpeg as ffmpeg_bin

    root = Path(workdir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}
    sources = _sources(plan)
    out: Dict[int, str] = {}
    for shot in _visual_shots(_shots(plan)):
        ref = sources.get(shot["source_id"])
        path = str(_field(ref, "path", "") or "")
        if not path or not Path(path).is_file():
            continue
        length = max(0.0, shot["src_out"] - shot["src_in"])
        at = shot["src_in"] + length * 0.5
        png = root / f"shot_{shot['index']:03d}.png"
        try:
            proc = subprocess.run(
                [
                    ffmpeg_bin(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(png),
                ],
                capture_output=True,
            )
        except OSError:
            continue
        if proc.returncode == 0 and png.is_file():
            out[shot["index"]] = str(png)
    return out


# --------------------------------------------------------------------------- #
# provenienza dei tagli e apertura
# --------------------------------------------------------------------------- #


def _reason_from_value(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        for key in ("kind", "source", "type", "reason", "provenance"):
            found = value.get(key)
            if isinstance(found, str) and found in CUT_SOURCES:
                return found
        return None
    if isinstance(value, str) and value in CUT_SOURCES:
        return value
    return None


def _explicit_reason(shot: Mapping[str, Any]) -> Optional[str]:
    for record in shot.get("provenance") or ():
        found = _reason_from_value(record)
        if found:
            return found
    return None


def _plan_cut_reason(plan: Any, at: float) -> Optional[str]:
    data = _as_dict(plan)
    cuts = data.get("cuts")
    if not isinstance(cuts, (list, tuple)):
        return None
    for cut in cuts:
        when = _number(_field(cut, "t"))
        if when is None:
            when = _number(_field(cut, "at"))
        if when is None:
            when = _number(_field(cut, "dst_in"))
        if when is None or abs(when - at) > CUT_TOLERANCE:
            continue
        found = _reason_from_value(
            _field(cut, "provenance") or _field(cut, "reason") or _field(cut, "cause")
        )
        if found:
            return found
    return None


def _anchor_reason(bridge: Mapping[str, Any], source_id: str, at: float) -> Optional[str]:
    """La provenienza che un'ancora del ponte da' a un taglio a ``at``.

    ``silence_span`` vale ``speech``: il taglio toglie il silenzio attorno alle
    parole, quindi e' il parlato a deciderlo. Una parola vale solo sul suo
    inizio o alla sua fine: un taglio dentro la parola non e' giustificato.
    """
    for anchor in bridge.get("anchors") or ():
        if not isinstance(anchor, Mapping):
            continue
        if str(anchor.get("source_id") or "") != source_id:
            continue
        kind = _ANCHOR_SOURCES.get(str(anchor.get("kind") or ""))
        if not kind:
            continue
        t0 = _number(anchor.get("t0"))
        t1 = _number(anchor.get("t1"))
        if t0 is None:
            continue
        if kind == "speech" and str(anchor.get("kind") or "") == "silence_span":
            if t1 is not None and t0 - CUT_TOLERANCE <= at <= t1 + CUT_TOLERANCE:
                return kind
            continue
        tolerance = WORD_TOLERANCE if kind == "speech" else CUT_TOLERANCE
        points = [t0] + ([t1] if t1 is not None else [])
        if any(abs(at - point) <= tolerance for point in points):
            return kind
        if t1 is not None and t0 <= at <= t1:
            return kind
    return None


def _bridge_cut_reason(bridge: Mapping[str, Any], at: float) -> Optional[str]:
    """Un taglio che il ponte dichiara ``dead_air`` e' un taglio sul parlato."""
    for cut in bridge.get("cuts") or ():
        if not isinstance(cut, Mapping):
            continue
        when = _number(cut.get("t"))
        if when is None or abs(when - at) > 0.35:
            continue
        if str(cut.get("reason") or "") == "dead_air":
            return "speech"
    return None


def _cut_reason(
    plan: Any,
    bridge: Mapping[str, Any],
    previous: Mapping[str, Any],
    following: Mapping[str, Any],
) -> Optional[str]:
    """La provenienza di un taglio, dalla piu' esplicita alla piu' derivata."""
    for shot in (previous, following):
        found = _explicit_reason(shot)
        if found:
            return found
    at = previous["dst_out"]
    found = _plan_cut_reason(plan, at)
    if found:
        return found
    if bridge:
        same_source = previous["source_id"] == following["source_id"]
        source_id = previous["source_id"] if same_source else following["source_id"]
        anchor_at = previous["src_out"] if same_source else following["src_in"]
        found = _anchor_reason(bridge, source_id, anchor_at)
        if found:
            return found
        if same_source:
            found = _bridge_cut_reason(bridge, previous["src_out"])
            if found:
                return found
    return None


def _provenance_channel(plan: Any, shots: Sequence[Mapping[str, Any]], bridge: Mapping[str, Any]) -> str:
    """Da dove arriva la provenienza: ``plan``, ``report`` o nessuna.

    Nessuna provenienza non e' un errore: e' un piano vecchio, che non sapeva
    di doverla dichiarare. Il referto lo dice come ``unknown``.
    """
    if any(_explicit_reason(shot) for shot in shots):
        return "plan"
    data = _as_dict(plan)
    for cut in data.get("cuts") or ():
        if _reason_from_value(
            _field(cut, "provenance") or _field(cut, "reason") or _field(cut, "cause")
        ):
            return "plan"
    if bridge and (bridge.get("anchors") or bridge.get("cuts")):
        return "report"
    return "unknown"


def _opening_evidence(
    plan: Any,
    bridge: Mapping[str, Any],
    first: Mapping[str, Any],
) -> Tuple[str, str]:
    """``hook`` / ``text`` / ``none`` per il primo shot, con la prova."""
    for record in first.get("provenance") or ():
        for key in ("kind", "source", "type", "reason", "provenance"):
            value = record.get(key) if isinstance(record, Mapping) else record
            if isinstance(value, str) and value in ("hook", "opening"):
                return "hook", f"provenienza del primo shot: {value}"

    data = _as_dict(plan)
    for treatment in data.get("treatments") or ():
        if not isinstance(treatment, Mapping):
            continue
        if str(treatment.get("name") or "") != "hook_overlay":
            continue
        if treatment.get("enabled") is True:
            return "hook", "trattamento hook_overlay attivo"

    for anchor in bridge.get("anchors") or ():
        if not isinstance(anchor, Mapping):
            continue
        if str(anchor.get("kind") or "") not in ("hook", "opening"):
            continue
        return "hook", f"ancora {anchor.get('kind')} del ponte"

    for caption in bridge.get("captions") or ():
        if not isinstance(caption, Mapping):
            continue
        start = _number(caption.get("dst_in"))
        text = " ".join(str(caption.get("text") or "").split())
        if not text or start is None:
            continue
        window = min(first["dst_out"], float(THRESHOLDS["hook_window"]))
        if start < window - 1e-6:
            return "text", f"primo verso a {start:.2f}s: {text[:48]!r}"
    return "none", ""


# --------------------------------------------------------------------------- #
# il referto
# --------------------------------------------------------------------------- #


def _violation(
    code: str, evidence: str, **metrics: Any
) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": "warn",
        "evidence": evidence,
        "metrics": metrics,
    }


def _runs(hashes: Mapping[int, str], order: Sequence[int]) -> List[List[int]]:
    """Le sequenze consecutive di shot quasi identici, lunghe almeno due.

    Un fotogramma mancante interrompe la sequenza: non si salta il buco, perche'
    due shot identici separati da un frame non letto non sono consecutivi.
    """
    runs: List[List[int]] = []
    current: List[int] = []
    for index in order:
        if index not in hashes:
            if current:
                runs.append(current)
            current = []
            continue
        if not current:
            current = [index]
            continue
        distance = _distance(hashes[current[-1]], hashes[index])
        if distance is not None and distance < SHOT_SAME_DISTANCE:
            current.append(index)
        else:
            runs.append(current)
            current = [index]
    if current:
        runs.append(current)
    return runs


def disabled(reason: str = "--free") -> Dict[str, Any]:
    """Il referto quando l'utente esonera il montaggio dai controlli.

    Non e' un ``ok`` pulito: e' un ``ok`` che si dichiara spento, cosi' chi legge
    il report sa che nessuno ha guardato.
    """
    return {
        "ok": True,
        "disabled": True,
        "reason": f"{reason}: controlli di coerenza disattivati per scelta esplicita",
        "violations": [],
        "metrics": {"checked": False},
    }


def evaluate(
    plan: Any,
    report: Any = None,
    *,
    bridge: Any = None,
    frames: Any = None,
    text_required: bool = False,
) -> Dict[str, Any]:
    """Il referto di coerenza del piano.

    ``plan`` e' l'EDL (mapping di ``plan.json`` o oggetto ``EDLPlan``),
    ``report``/``bridge`` il referto del ponte. ``frames`` mappa indice shot ->
    percorso PNG e serve ai test (e a un chiamante che li ha gia' estratti).
    ``text_required`` dice se l'apertura doveva portare il primo verso: senza
    testo richiesto, l'unico motivo ammesso in apertura e' il hook.
    """
    report = report if isinstance(report, Mapping) else _as_dict(report)
    bridge_map = _bridge_dict(report, bridge)
    clips = _shots(plan)
    shots = _visual_shots(clips)
    thresholds = {
        "shot_same_distance": SHOT_SAME_DISTANCE,
        "min_variety": MIN_VARIETY,
        "repeat_run": REPEAT_RUN,
        "black_luma": BLACK_LUMA,
        "sharpness_low": float(THRESHOLDS["triage_sharpness_low"]),
        "hook_window": float(THRESHOLDS["hook_window"]),
        "cut_tolerance_sec": CUT_TOLERANCE,
    }

    metrics: Dict[str, Any] = {
        "shots": len(shots),
        "clips": len(clips),
        "frames_measured": 0,
        "distinct_shots": None,
        "variety": None,
        "repeated_runs": [],
        "black_frames": [],
        "blurred_frames": [],
        "cuts": max(0, len(clips) - 1),
        "justified_cuts": None,
        "unjustified_cuts": None,
        "cut_provenance": "unknown",
        "opening": "unknown",
        "thresholds": thresholds,
        "unknown": [],
    }
    violations: List[Dict[str, Any]] = []
    unknown: List[Dict[str, str]] = metrics["unknown"]

    if not shots:
        unknown.append({"check": "plan", "reason": "il piano non ha clip di layer 0"})
        metrics["cut_provenance"] = "unknown"
        metrics["opening"] = "unknown"
        return {"ok": True, "violations": violations, "metrics": metrics}

    # -- fotogrammi -------------------------------------------------------- #
    hashes: Dict[int, str] = {}
    lumas: Dict[int, float] = {}
    sharpness: Dict[int, float] = {}
    unreadable: List[int] = []
    for shot in shots:
        index = shot["index"]
        path = _frame_path(frames, index, shot["clip_id"])
        if not path:
            unreadable.append(index)
            continue
        try:
            gray = _gray(path)
        except Exception:  # noqa: BLE001 - il frame c'e' ma non si legge
            unreadable.append(index)
            continue
        hashes[index] = _fingerprint(gray)
        try:
            lumas[index] = float(gray.mean()) / 255.0
        except Exception:  # noqa: BLE001
            unreadable.append(index)
            hashes.pop(index, None)
            continue
        value = _sharpness(gray)
        if value is not None:
            sharpness[index] = value
    metrics["frames_measured"] = len(hashes)
    if unreadable:
        unknown.append(
            {
                "check": "frames",
                "reason": f"{len(unreadable)} shot senza fotogramma: {unreadable}",
            }
        )

    # -- varieta' ---------------------------------------------------------- #
    distinct = None
    if len(hashes) < 2:
        unknown.append(
            {
                "check": "variety",
                "reason": f"servono almeno 2 fotogrammi, misurati {len(hashes)}",
            }
        )
    elif len(hashes) < len(shots):
        unknown.append(
            {
                "check": "variety",
                "reason": "non tutti gli shot hanno un fotogramma: il rapporto "
                "distinti/totali sarebbe inventato",
            }
        )
    else:
        groups: List[List[int]] = []
        for index in sorted(hashes):
            for group in groups:
                distance = _distance(hashes[group[0]], hashes[index])
                if distance is not None and distance < SHOT_SAME_DISTANCE:
                    group.append(index)
                    break
            else:
                groups.append([index])
        distinct = len(groups)
        variety = distinct / float(len(shots))
        metrics["distinct_shots"] = distinct
        metrics["variety"] = round(variety, 3)
        if variety < MIN_VARIETY:
            violations.append(
                _violation(
                    "low_variety",
                    f"varieta' {variety:.2f} sotto {MIN_VARIETY:.2f}: "
                    f"{distinct} shot distinti su {len(shots)}",
                    distinct=distinct,
                    total=len(shots),
                    ratio=round(variety, 3),
                )
            )

    # -- ripetizione ------------------------------------------------------- #
    order = [shot["index"] for shot in shots]
    runs = [
        run
        for run in _runs(hashes, order)
        if len(run) >= REPEAT_RUN
    ]
    metrics["repeated_runs"] = runs
    for run in runs:
        violations.append(
            _violation(
                "repeated_shots",
                f"shot {run[0]}-{run[-1]} quasi identici: "
                f"{len(run)} inquadrature consecutive ripetute",
                indices=list(run),
                count=len(run),
            )
        )

    # -- frame inutilizzabili --------------------------------------------- #
    for index in sorted(lumas):
        luma = lumas[index]
        if luma < BLACK_LUMA:
            metrics["black_frames"].append(
                {"index": index, "luma": round(luma, 4)}
            )
            violations.append(
                _violation(
                    "unusable_frame",
                    f"shot {index}: frame nero, luma media {luma:.3f} "
                    f"sotto {BLACK_LUMA:.2f}",
                    index=index,
                    reason="black",
                    luma=round(luma, 4),
                )
            )
            continue
        value = sharpness.get(index)
        if value is None:
            continue
        limit = float(THRESHOLDS["triage_sharpness_low"])
        if value < limit:
            metrics["blurred_frames"].append(
                {"index": index, "sharpness": round(value, 2)}
            )
            violations.append(
                _violation(
                    "unusable_frame",
                    f"shot {index}: immagine mossa/uniforme, nitidezza {value:.1f} "
                    f"sotto {limit:.1f} (la soglia del triage)",
                    index=index,
                    reason="motion_blur",
                    sharpness=round(value, 2),
                )
            )
    if not sharpness:
        unknown.append(
            {
                "check": "sharpness",
                "reason": "nitidezza non misurabile: OpenCV assente",
            }
        )

    # -- apertura ---------------------------------------------------------- #
    opening, _proof = _opening_evidence(plan, bridge_map, shots[0])
    metrics["opening"] = opening
    if opening == "none":
        expected = "hook o primo verso del testo" if text_required else "hook"
        window = min(shots[0]["dst_out"], float(THRESHOLDS["hook_window"]))
        violations.append(
            _violation(
                "weak_open",
                f"il primo shot non ha un motivo ({expected}): nessuna ancora, "
                "nessuna provenienza e nessun verso nei primi "
                f"{window:.2f}s",
                index=shots[0]["index"],
                expected=expected,
            )
        )

    # -- ragione dei tagli ------------------------------------------------- #
    channel = _provenance_channel(plan, clips, bridge_map)
    metrics["cut_provenance"] = channel
    total_cuts = max(0, len(clips) - 1)
    if channel == "unknown":
        unknown.append(
            {
                "check": "cuts",
                "reason": "il piano non dichiara provenienze (beat/scene/speech/"
                "chorus) e il referto del ponte non porta ancore: "
                "piano vecchio, nessun taglio giudicato",
            }
        )
        metrics["justified_cuts"] = None
        metrics["unjustified_cuts"] = None
    else:
        reasons: List[Optional[str]] = []
        missing: List[int] = []
        for previous, following in zip(clips, clips[1:]):
            reason = _cut_reason(plan, bridge_map, previous, following)
            reasons.append(reason)
            if reason is None:
                missing.append(previous["index"])
        metrics["justified_cuts"] = total_cuts - len(missing)
        metrics["unjustified_cuts"] = len(missing)
        if missing:
            violations.append(
                _violation(
                    "unjustified_cut",
                    f"{len(missing)} tagli su {total_cuts} senza provenienza "
                    f"(beat/scene/speech/chorus): dopo i clip {missing}",
                    count=len(missing),
                    total=total_cuts,
                    indices=missing,
                )
            )

    return {"ok": not violations, "violations": violations, "metrics": metrics}
