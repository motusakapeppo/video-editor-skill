"""vx.animplan — l'asse animazione: tre effetti eseguiti scelti dal transient ratio.

Perche' esiste: ``registry`` dichiara quattro nomi (``none``, ``fade``, ``wipe``,
``punch``) e un solo motore (``ffmpeg``), ma non sa quale nome serva a un clip.
La scelta era implicita nel renderer e quindi non lasciava traccia. Qui la scelta
e' un oggetto: porta il driver, il valore misurato, la soglia con la sua
provenienza, la confidenza e la frase di inazione.

Il driver e' ``transient_ratio`` = ``p90(Δluma agli onset di parola ±2 frame) /
median(Δluma)``, calcolato SULLA SORGENTE. La formula e' copiata verbatim da
``tests/measure_corpus.py`` (W0): stessa riduzione a 320 px, stessa conversione
in grigio, stessa finestra ±2 frame, stesso ``round(..., 4)``. Se le due
implementazioni divergono, il selftest fallisce invece di aggiustare la
tolleranza.

Le soglie non sono inventate: ``p33`` e ``p67`` vengono da
``registry.THRESHOLDS["animation"]``, cioe' dal corpus misurato. **Il campione e'
piccolo: n = 3 di 9 clip**, perche' solo tre clip hanno parole. La direzione
``ratio↑ -> animazione piu' dura`` e' ASSERITA, non misurata; solo i punti di
taglio sono empirici. Il fatto e' scritto qui e nel campo ``note`` di ogni
decisione.

Regola dell'incertezza: se la confidenza cade in ``[0.2, 0.8]`` il nome e'
``none`` e ``applied`` e' ``False``. La confidenza e' una probabilita': vale
``0.5`` esattamente sul punto di taglio e sale verso ``1.0`` allontanandosi,
normalizzata su un decimo dell'ampiezza misurata ``p67 - p33``. Il centro della
banda ``wipe`` e' un ``wipe`` sicuro, un valore sul punto di taglio e' un dubbio.
Il decimo e' dichiarato, non misurato: e' la zona morta attorno al taglio, non
una soglia nuova.

I tre frammenti sono filtri ffmpeg single-stream e preservano il numero di
frame: ``fade`` e ``wipe`` non cambiano il rate, ``punch`` usa ``zoompan`` con
``d=1`` e ``fps`` uguale a quello d'ingresso, e ``s=WxH`` uguale alla geometria
d'ingresso. Nessun secondo input, nessun ``-c copy``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import probe
from .registry import ANIMATIONS, THRESHOLDS

# Stessa larghezza di lavoro di analyze._pixel_metrics e di measure_corpus.
WORK_WIDTH = 320
# Durata di default quando lo shot non la dichiara (secondi).
DEFAULT_DURATION = 0.25
# Finestra ±2 frame attorno all'onset, come W0.
ONSET_HALF_WINDOW = 2
# Oltre questo rate di onset il punch puo' raddoppiare, mai oltre 2.
HIGH_ONSET_RATE = 3.5
MAX_COUNT = 2
UNCERTAIN_LOW = 0.2
UNCERTAIN_HIGH = 0.8
# La confidenza e' una probabilita': 0.5 sul punto di taglio, 1.0 lontano.
# La distanza e' normalizzata su un decimo dell'ampiezza misurata p67 - p33:
# e' la zona morta dichiarata attorno al taglio, non una soglia nuova.
CONFIDENCE_SPAN_FRACTION = 0.1

SMALL_SAMPLE_NOTE = (
    "transient_ratio p33/p67 misurate su n=3 di 9 clip (solo clip con parole): "
    "campione piccolo, direzione asserita."
)


def _thresholds() -> Dict[str, Any]:
    entry = THRESHOLDS.get("animation") or {}
    return {
        "p33": entry.get("p33"),
        "p67": entry.get("p67"),
        "provenance": entry.get("provenance") or "unmeasured",
    }


def _frame_diffs(path: Any, fps: float) -> Any:
    """Per-frame mean |Δluma| at 320 px, mirroring analyze._pixel_metrics.

    Copiata verbatim da ``tests/measure_corpus.py``: la formula W0 non si
    reinventa. ``fps`` uguale al rate della sorgente rende ``step == 1`` e
    allinea l'indice del frame al tempo.
    """
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(path))
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if source_fps <= 0.0:
        source_fps = float(fps) if fps > 0.0 else 25.0
    step = max(1, int(round(source_fps / float(fps)))) if fps > 0.0 else 1

    previous: Any = None
    per_frame: List[Optional[float]] = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % step:
            index += 1
            continue
        height, width = frame.shape[:2]
        small_h = max(2, int(round(height * WORK_WIDTH / float(width))))
        small = cv2.resize(frame, (WORK_WIDTH, small_h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if previous is None:
            per_frame.append(None)
        else:
            diff = float(
                np.mean(np.abs(gray.astype(np.float32) - previous.astype(np.float32)))
            )
            per_frame.append(diff)
        previous = gray
        index += 1
    capture.release()
    return per_frame, source_fps


def _onsets(words: Any) -> List[float]:
    """Accept ``subs.Transcript.words`` (dicts) or a plain list of floats."""
    out: List[float] = []
    for word in words or []:
        if isinstance(word, dict):
            out.append(float(word["start"]))
        else:
            out.append(float(word))
    return out


def transient(path: Any, words: Any) -> Optional[float]:
    """``p90(Δluma agli onset ±2 frame) / median(Δluma)``, W0 bit-for-bit.

    ``words`` is ``subs.transcribe(path).words`` (a list of dicts with ``start``)
    or a list of onset seconds. Returns ``None`` when there are no words, fewer
    than two diffs, or a zero median: a missing number is honest, a made-up one
    is not.
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 - the toolkit stays importable without cv2
        return None

    info = probe.probe(str(path))
    fps = float(info.fps)
    try:
        per_frame, source_fps = _frame_diffs(path, fps)
    except Exception:  # noqa: BLE001 - no decoder, no measurement
        return None

    onsets = _onsets(words)
    values = [value for value in per_frame if value is not None]
    if len(values) < 2 or not onsets:
        return None
    median = float(np.median(values))
    if median <= 0.0:
        return None

    window: List[float] = []
    for onset in onsets:
        frame = int(round(float(onset) * source_fps))
        for offset in range(frame - ONSET_HALF_WINDOW, frame + ONSET_HALF_WINDOW + 1):
            if 0 <= offset < len(per_frame) and per_frame[offset] is not None:
                window.append(float(per_frame[offset]))
    if len(window) < 2:
        return None
    return round(float(np.percentile(window, 90)) / median, 4)


def _none(
    value: Optional[float],
    provenance: str,
    inaction: str,
    confidence: float,
) -> Dict[str, Any]:
    return {
        "name": "none",
        "count": 0,
        "driver": "transient_ratio",
        "value": value,
        "provenance": provenance,
        "inaction": inaction,
        "confidence": round(float(confidence), 4),
        "applied": False,
        "direction_basis": "asserted",
        "note": SMALL_SAMPLE_NOTE,
    }


def decide(
    ratio: Optional[float], onsets_per_s: Optional[float] = None
) -> Dict[str, Any]:
    """Choose the animation name and count from the measured transient ratio.

    ``ratio < p33`` -> ``fade``; ``p33 <= ratio <= p67`` -> ``wipe``;
    ``ratio > p67`` -> ``punch``. A ``None`` ratio, unmeasured thresholds, or a
    confidence in ``[0.2, 0.8]`` all return ``none`` with ``applied: False``.
    ``count`` is 1, or 2 only for a confident ``punch`` when the caller reports
    ``onsets_per_s > 3.5``; it is never more than 2.
    """
    th = _thresholds()
    p33, p67, provenance = th["p33"], th["p67"], th["provenance"]

    if ratio is None:
        return _none(
            None,
            provenance,
            "transient_ratio non misurata (nessuna parola): nessuna animazione.",
            0.5,
        )
    if p33 is None or p67 is None:
        return _none(
            float(ratio),
            provenance,
            "soglie transient_ratio non misurate: nessuna animazione.",
            0.5,
        )

    ratio = float(ratio)
    p33, p67 = float(p33), float(p67)
    spread = p67 - p33
    span = CONFIDENCE_SPAN_FRACTION * spread if spread > 0.0 else 0.0

    if ratio < p33:
        distance = p33 - ratio
    elif ratio <= p67:
        distance = min(ratio - p33, p67 - ratio)
    else:
        distance = ratio - p67
    confidence = 0.5 + 0.5 * min(1.0, distance / span) if span > 0.0 else 1.0

    if UNCERTAIN_LOW <= confidence <= UNCERTAIN_HIGH:
        return _none(
            ratio,
            provenance,
            (
                f"confidenza {confidence:.3f} in [{UNCERTAIN_LOW}, {UNCERTAIN_HIGH}]: "
                "inazione, nessuna animazione."
            ),
            confidence,
        )

    if ratio < p33:
        name = "fade"
        sentence = f"transient_ratio {ratio:.4f} < p33 {p33:.4f}: fade."
    elif ratio <= p67:
        name = "wipe"
        sentence = (
            f"p33 {p33:.4f} <= transient_ratio {ratio:.4f} <= p67 {p67:.4f}: wipe."
        )
    else:
        name = "punch"
        sentence = f"transient_ratio {ratio:.4f} > p67 {p67:.4f}: punch."

    count = 1
    if (
        name == "punch"
        and onsets_per_s is not None
        and float(onsets_per_s) > HIGH_ONSET_RATE
    ):
        count = 2
    count = min(count, MAX_COUNT)

    return {
        "name": name,
        "count": count,
        "driver": "transient_ratio",
        "value": ratio,
        "provenance": provenance,
        "inaction": sentence,
        "confidence": round(float(confidence), 4),
        "applied": True,
        "direction_basis": "asserted",
        "note": SMALL_SAMPLE_NOTE,
    }


def _shot_field(shot: Any, key: str, default: Any = None) -> Any:
    if shot is None:
        return default
    if isinstance(shot, dict):
        return shot.get(key, default)
    return getattr(shot, key, default)


def chain_for_shot(choice: Dict[str, Any], shot: Any = None) -> str:
    """The ffmpeg single-stream fragment for the chosen animation.

    ``none`` returns the empty string: the caller must not add a filter that
    does not exist. ``fade`` and ``wipe`` need only the duration; ``punch`` also
    needs the input geometry (``width``/``height``/``fps``) so ``zoompan`` keeps
    ``s=WxH`` equal to the input size. The fragment has no leading or trailing
    comma, so ``a + "," + chain_for_shot(...)`` stays a valid graph.
    """
    name = str((choice or {}).get("name") or "none")
    if name not in ANIMATIONS:
        raise ValueError(f"animazione sconosciuta: {name!r}")
    if name == "none":
        return ""

    duration = _shot_field(shot, "duration", DEFAULT_DURATION)
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION
    if duration <= 0.0:
        duration = DEFAULT_DURATION

    template = str(ANIMATIONS[name]["template"])
    fragment = template.replace("{d}", f"{duration:.4f}")

    if name == "punch":
        width = _shot_field(shot, "width")
        height = _shot_field(shot, "height")
        fps = _shot_field(shot, "fps")
        if width is None or height is None or fps is None:
            path = _shot_field(shot, "path")
            if path:
                info = probe.probe(str(path))
                width = width if width is not None else info.width
                height = height if height is not None else info.height
                fps = fps if fps is not None else info.fps
        if width is None or height is None or fps is None:
            raise ValueError(
                "punch needs the input geometry (width/height/fps) to keep the "
                "output size equal to the input size"
            )
        fragment = (
            fragment.replace("{w}", str(int(width)))
            .replace("{h}", str(int(height)))
            .replace("{fps}", f"{float(fps):g}")
        )
    return fragment


def _run_effect(src: Any, fragment: str, out: Any) -> Any:
    from . import ffmpeg as ff

    return ff.run_ff(
        [
            "-i",
            str(src),
            "-vf",
            fragment,
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "26",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        loglevel="error",
    )


def _selftest() -> int:
    import json
    from pathlib import Path

    from . import subs

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")

    root = Path(__file__).resolve().parents[2]
    corpus = root / "tests" / "stock_corpus"
    stats_path = root / "tests" / "fixtures" / "corpus_stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    th = _thresholds()

    print("soglie transient_ratio, con provenienza:")
    print(f"  p33 = {th['p33']}  p67 = {th['p67']}  ({th['provenance']})")
    print(
        "  n = 3 di 9 clip (solo clip con parole): campione piccolo, direzione asserita"
    )

    word_clips = [clip for clip in stats["clips"] if clip.get("words")]
    for clip in word_clips:
        path = corpus / clip["path"]
        transcript = subs.transcribe(str(path))
        got = transient(path, transcript.words)
        stored = clip["transient_ratio"]
        ok = (
            got is not None
            and stored is not None
            and abs(float(got) - float(stored)) <= 1e-6
        )
        check(
            f"{clip['name']}: transient == stored",
            ok,
            f"{got} vs {stored}",
        )
        choice = decide(got)
        print(
            f"    scelta: {choice['name']} count={choice['count']} "
            f"conf={choice['confidence']} applied={choice['applied']} "
            f"(p33={th['p33']}, p67={th['p67']})"
        )
        check(
            f"{clip['name']}: count <= {MAX_COUNT}",
            choice["count"] <= MAX_COUNT,
            str(choice["count"]),
        )

    if th["p33"] is not None and th["p67"] is not None:
        p33, p67 = float(th["p33"]), float(th["p67"])
        print("\nmappatura del driver su valori sintetici (dimostra i tre nomi):")
        for label, value in (
            ("sotto p33", p33 - 1.0),
            ("in banda", (p33 + p67) / 2.0),
            ("sopra p67", p67 + 1.0),
        ):
            mapped = decide(value)
            print(
                f"  {label:10} ratio={value:.4f} -> {mapped['name']} "
                f"count={mapped['count']} conf={mapped['confidence']} "
                f"applied={mapped['applied']}"
            )
            check(
                f"mappatura {label} -> {mapped['name']}",
                mapped["applied"] is True,
                str(mapped["confidence"]),
            )

    none_choice = decide(None)
    check("decide(None) -> none", none_choice["name"] == "none", none_choice["name"])
    check(
        "decide(None) -> count 0", none_choice["count"] == 0, str(none_choice["count"])
    )
    check(
        "decide(None) -> applied False",
        none_choice["applied"] is False,
        str(none_choice["applied"]),
    )
    check(
        "decide(None) -> frase di inazione",
        bool(none_choice["inaction"]),
        none_choice["inaction"],
    )

    src = corpus / "bright_45.mp4"
    info = probe.probe(str(src))
    shot = {
        "duration": 0.25,
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
    }
    out_dir = root / "tests" / "_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nesecuzione dei tre effetti su {src.name} ({info.nb_frames} frame):")
    for name in ("fade", "wipe", "punch"):
        fragment = chain_for_shot({"name": name, "count": 1}, shot)
        out = out_dir / f"animplan_{name}.mp4"
        proc = _run_effect(src, fragment, out)
        check(f"{name}: ffmpeg rc=0", proc.rc == 0, proc.tail(2) if proc.rc else "")
        if proc.rc == 0:
            got = probe.probe(str(out), count_frames=True)
            check(
                f"{name}: frame preservati",
                got.nb_frames == info.nb_frames,
                f"{got.nb_frames} vs {info.nb_frames}",
            )
            check(
                f"{name}: dimensione preservata",
                (got.width, got.height) == (info.width, info.height),
                f"{got.width}x{got.height}",
            )
        else:
            check(f"{name}: frame preservati", False, "ffmpeg fallito")
            check(f"{name}: dimensione preservata", False, "ffmpeg fallito")

    print(f"{'OK' if failures == 0 else 'FALLITO'} — {failures} fallimenti")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vx.animplan selftest")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    parser.print_help()
    raise SystemExit(2)
