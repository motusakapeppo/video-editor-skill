"""vx.lyrics -- la spec del testo animato, verificata prima di guardarla.

Perche' esiste: nel caso "Scendi" sette versioni sono state spese su quattro
difetti che una macchina poteva vedere -- una parola sbagliata ("sono" per
"sotto", "modo" per "mondo"), un effetto su un colpo su due, un loop che non
chiude, un testo che non si legge. La spec e' un JSON: si controlla senza
renderizzare, e si controlla **prima** del render.

Il comando e' ``vx lyrics check``; la forma della spec e' in
``references/32-montaggio-lirico.md`` §3. Ogni controllo ha un id (``L1``...) e
tre esiti possibili: ``ok``, ``warn`` (misura stimata o non bloccante), ``fail``.
Un controllo che non puo' essere fatto dichiara ``skip`` e il motivo -- non
finge di essere passato.

Nessun controllo inventa un numero: quelli che leggono il master usano
``vx.probe`` e ``vx.audio``, quelli che leggono il testo usano il file scritto.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Il target di consegna: lo stesso che dichiara `references/08-qc-e-consegna.md`.
TARGET_LUFS = -14.0
LUFS_TOLERANCE = 1.5
TRUE_PEAK_CEILING = -1.0

#: Quanto un fotogramma puo' differire dal suo vicino di loop, per pixel.
#: La tolleranza e' quella dei golden del repo (x264 lascia pochi livelli su
#: qualche pixel al bordo di un GOP); un taglio vero sbaglia di molto.
LOOP_DELTA = 8
LOOP_SHARE = 0.0002

#: Una finestra puo' portare una pick-up (l'anacrusi prima del battere 1): il
#: conteggio delle battute si tollera a mezza battuta, non a una intera.
BAR_SLACK = 0.5

#: Larghezza media di un glifo in em per un grottesco pesante maiuscolo. E' una
#: **stima** e per questo il controllo che la usa e' `warn`, non `fail`.
GLYPH_EM = 0.62
LINE_MARGIN = 0.94


# ------------------------------------------------------------------ lettura


def load(path: str | Path) -> Dict[str, Any]:
    """Legge la spec e rifiuta cio' che non e' nemmeno un documento.

    Un JSON che non e' un oggetto, o che non dichiara ne' ``events`` ne'
    ``duration``, e' un errore d'uso: non e' una spec con dei difetti, e' un
    altro file.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{Path(path).name}: la spec deve essere un oggetto JSON")
    if "events" not in raw and "duration" not in raw:
        raise ValueError(
            f"{Path(path).name}: manca sia 'events' sia 'duration': non e' una spec"
        )
    return dict(raw)


# ------------------------------------------------------------------ testo


def fold(text: str) -> str:
    """Minuscolo, senza accenti, apostrofi tipografici ridotti a ``'``."""
    text = str(text).replace("\u2019", "'").replace("\u2018", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def words(text: str) -> List[str]:
    """Le parole di una riga, nell'ordine; la punteggiatura non e' una parola."""
    cleaned = re.sub(r"[^0-9A-Za-zÀ-ÿ']+", " ", fold(text))
    return [item for item in cleaned.split() if item]


#: I segni che un UTF-8 letto come cp1252 lascia dietro di se'. Il caso vero:
#: ``10 - SCENDI.txt`` conteneva ``cosÃ¬`` invece di ``così``, e il confronto
#: parola per parola accusava la spec di un errore che era nel file.
_MOJIBAKE = ("Ã", "Â", "â€")


def _repair_token(token: str) -> str:
    for encoding in ("cp1252", "latin-1"):
        try:
            fixed = token.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != token:
            return fixed
    return token


def repair_text(text: str) -> Tuple[str, str]:
    """``(testo, nota)``: ripristina un testo doppiamente codificato, se lo e'.

    Token per token: un carattere irrecuperabile in una riga non deve impedire
    il ripristino di tutte le altre. La nota dice quanti token sono stati
    ripristinati e quanti restano illeggibili; se non c'era niente da fare, e'
    vuota.
    """
    if not any(mark in text for mark in _MOJIBAKE):
        return text, ""
    pieces = re.split(r"(\s+)", text)
    out: List[str] = []
    repaired = 0
    broken = 0
    for piece in pieces:
        if not piece or piece.isspace():
            out.append(piece)
            continue
        fixed = _repair_token(piece)
        if fixed != piece:
            repaired += 1
        if "\ufffd" in fixed:
            broken += 1
        out.append(fixed)
    note = f"testo doppiamente codificato: {repaired} parole ripristinate"
    if broken:
        note += f", {broken} illeggibili"
    return "".join(out), note


def load_text(path: str | Path) -> Tuple[str, str]:
    """Il testo scritto della canzone, con la nota sul ripristino (o ``""``)."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    return repair_text(raw)


def _line_text(line: Any) -> str:
    if isinstance(line, Mapping):
        return str(line.get("text") or "")
    return str(line or "")


def _line_emphases(line: Any) -> List[str]:
    if not isinstance(line, Mapping):
        return []
    value = line.get("em")
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


# ------------------------------------------------------------------ righe di report


def _row(
    ident: str, label: str, status: str, detail: Any = ""
) -> Dict[str, Any]:
    return {"id": ident, "label": label, "status": status, "detail": str(detail)}


def _ok(ident: str, label: str, detail: Any = "") -> Dict[str, Any]:
    return _row(ident, label, "ok", detail)


def _warn(ident: str, label: str, detail: Any = "") -> Dict[str, Any]:
    return _row(ident, label, "warn", detail)


def _fail(ident: str, label: str, detail: Any = "") -> Dict[str, Any]:
    return _row(ident, label, "fail", detail)


def _skip(ident: str, label: str, why: str) -> Dict[str, Any]:
    return _row(ident, label, "skip", why)


# ------------------------------------------------------------------ controlli


def _check_document(spec: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    fps = float(spec.get("fps") or 0)
    duration = float(spec.get("duration") or 0)
    events = spec.get("events")
    rows.append(
        _ok("L1", "il documento dichiara fps, durata e finestre")
        if fps > 0 and duration > 0 and isinstance(events, list) and events
        else _fail(
            "L1",
            "il documento dichiara fps, durata e finestre",
            f"fps={fps:g} durata={duration:g} finestre={len(events) if isinstance(events, list) else 'assenti'}",
        )
    )
    frames = spec.get("frames")
    if frames is None or fps <= 0 or duration <= 0:
        rows.append(_skip("L2", "i fotogrammi dichiarati tornano", "frames o fps assenti"))
    else:
        want = int(round(duration * fps))
        rows.append(
            _ok("L2", "i fotogrammi dichiarati tornano", f"{frames} = {want}")
            if abs(int(frames) - want) <= 1
            else _fail(
                "L2", "i fotogrammi dichiarati tornano", f"{frames} invece di {want}"
            )
        )
    return rows


def _bar_count(event: Mapping[str, Any]) -> Optional[int]:
    """Quante battute copre una finestra: numero, oppure ``"5-8"`` = 4."""
    value = event.get("bars")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*-\s*\d+\s*", value):
        first, last = (int(part) for part in re.split(r"\s*-\s*", value.strip()))
        return last - first + 1 if last >= first else None
    return None


def _check_windows(
    spec: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Mapping[str, Any]], float, float]:
    events = [event for event in spec.get("events") or [] if isinstance(event, Mapping)]
    duration = float(spec.get("duration") or 0)
    fps = float(spec.get("fps") or 0)
    tol = 1.0 / max(fps, 1.0) + 1e-6
    rows: List[Dict[str, Any]] = []
    problems: List[str] = []
    previous_end = None
    for index, event in enumerate(events):
        try:
            start = float(event.get("t0"))
            end = float(event.get("t1"))
        except (TypeError, ValueError):
            problems.append(f"events[{index}]: t0/t1 non numerici")
            continue
        if start < 0:
            problems.append(f"events[{index}]: t0 {start:.3f} < 0")
        if end <= start:
            problems.append(f"events[{index}]: t1 {end:.3f} <= t0 {start:.3f}")
        if duration and end > duration + tol:
            problems.append(f"events[{index}]: t1 {end:.3f} oltre la durata {duration:.3f}")
        if previous_end is not None and start < previous_end - tol:
            problems.append(
                f"events[{index}]: si sovrappone alla finestra precedente "
                f"({start:.3f} < {previous_end:.3f})"
            )
        previous_end = end
    rows.append(
        _ok("L3", "le finestre sono ordinate, dentro la durata e senza sovrapposizioni")
        if not problems
        else _fail(
            "L3",
            "le finestre sono ordinate, dentro la durata e senza sovrapposizioni",
            "; ".join(problems[:3]) + (f" (+{len(problems) - 3})" if len(problems) > 3 else ""),
        )
    )
    return rows, events, duration, tol


def _check_coverage(
    spec: Mapping[str, Any], events: Sequence[Mapping[str, Any]], duration: float, tol: float
) -> List[Dict[str, Any]]:
    if not events or duration <= 0:
        return [_skip("L4", "la testa e la coda chiudono il loop", "nessuna finestra")]
    first = min(float(event.get("t0") or 0) for event in events)
    last = max(float(event.get("t1") or 0) for event in events)
    gaps = 0.0
    ordered = sorted(events, key=lambda item: float(item.get("t0") or 0))
    for left, right in zip(ordered, ordered[1:]):
        gap = float(right.get("t0") or 0) - float(left.get("t1") or 0)
        if gap > tol:
            gaps = max(gaps, gap)
    detail = f"testa {first:.3f} s, coda {last:.3f} s, buco massimo {gaps:.3f} s"
    closes = abs(first) <= tol and abs(last - duration) <= tol
    if closes:
        return [_ok("L4", "la testa e la coda chiudono il loop", detail)]
    loop_declared = isinstance(spec.get("structure"), Mapping) and bool(
        (spec["structure"] or {}).get("loop")
    )
    if loop_declared:
        return [
            _fail(
                "L4",
                "la testa e la coda chiudono il loop (dichiarato in structure.loop)",
                detail,
            )
        ]
    return [_warn("L4", "la testa e la coda chiudono il loop", detail + " — non dichiarato")]


def _check_lines(events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    problems: List[str] = []
    emphasis_problems: List[str] = []
    emphases = 0
    for index, event in enumerate(events):
        style = str(event.get("style") or "").strip()
        if not style:
            problems.append(f"events[{index}]: nessuno stile")
        lines = event.get("lines")
        if not isinstance(lines, list) or not lines:
            problems.append(f"events[{index}]: nessuna riga")
            continue
        for position, line in enumerate(lines):
            text = _line_text(line)
            if not text.strip():
                problems.append(f"events[{index}].lines[{position}]: testo vuoto")
            for emphasis in _line_emphases(line):
                wanted = words(emphasis)
                if not wanted:
                    emphasis_problems.append(
                        f"events[{index}].lines[{position}]: enfasi vuota"
                    )
                elif not set(wanted) <= set(words(text)):
                    emphasis_problems.append(
                        f"events[{index}].lines[{position}]: '{emphasis}' non e' nella riga"
                    )
                else:
                    emphases += 1
    return [
        _ok("L5", "ogni finestra ha stile e righe leggibili", f"{len(events)} finestre")
        if not problems
        else _fail("L5", "ogni finestra ha stile e righe leggibili", "; ".join(problems[:3])),
        _ok("L6", "ogni parola enfatizzata e' nella sua riga", f"{emphases} enfasi")
        if not emphasis_problems
        else _fail(
            "L6",
            "ogni parola enfatizzata e' nella sua riga",
            "; ".join(emphasis_problems[:3]),
        ),
    ]


def _check_hits(
    events: Sequence[Mapping[str, Any]], duration: float, tol: float
) -> List[Dict[str, Any]]:
    problems: List[str] = []
    total = 0
    for index, event in enumerate(events):
        raw = event.get("hits")
        if raw is None:
            continue
        if not isinstance(raw, list):
            problems.append(f"events[{index}]: 'hits' non e' una lista")
            continue
        try:
            hits = [float(value) for value in raw]
        except (TypeError, ValueError):
            problems.append(f"events[{index}]: un colpo non e' un numero")
            continue
        total += len(hits)
        if any(b <= a for a, b in zip(hits, hits[1:])):
            problems.append(f"events[{index}]: i colpi non sono in ordine crescente")
        start = float(event.get("t0") or 0)
        end = float(event.get("t1") or 0)
        outside = [hit for hit in hits if hit < start - tol or hit > end + tol]
        if outside:
            problems.append(
                f"events[{index}]: {len(outside)} colpi fuori dalla finestra "
                f"({outside[0]:.3f} non sta in {start:.3f}..{end:.3f})"
            )
        if duration and any(hit < -tol or hit > duration + tol for hit in hits):
            problems.append(f"events[{index}]: un colpo cade fuori dal video")
        word_hits = event.get("wordHits")
        if word_hits is not None:
            if not isinstance(word_hits, list):
                problems.append(f"events[{index}]: 'wordHits' non e' una lista")
            else:
                try:
                    marked = [float(value) for value in word_hits]
                except (TypeError, ValueError):
                    problems.append(f"events[{index}]: una parola-colpo non e' un numero")
                    continue
                stray = [
                    value
                    for value in marked
                    if not any(abs(value - hit) <= 1e-3 for hit in hits)
                ]
                if stray:
                    problems.append(
                        f"events[{index}]: {len(stray)} parole-colpo non sono colpi"
                    )
                lines = event.get("lines") or []
                if isinstance(lines, list) and marked and len(marked) != len(lines):
                    problems.append(
                        f"events[{index}]: {len(marked)} parole-colpo per "
                        f"{len(lines)} righe scritte"
                    )
    rows = [
        _ok("L7", "i colpi stanno nella loro finestra e sono in ordine", f"{total} colpi")
        if not problems
        else _fail("L7", "i colpi stanno nella loro finestra e sono in ordine", "; ".join(problems[:3])),
        _ok("L8", "ogni parola scritta ha il suo colpo")
        if not problems
        else _fail(
            "L8",
            "ogni parola scritta ha il suo colpo",
            "; ".join(item for item in problems if "parola" in item or "parole" in item)[:200]
            or "vedi L7",
        ),
    ]
    return rows


def _check_bars(events: Sequence[Mapping[str, Any]], tol: float) -> List[Dict[str, Any]]:
    """La matematica delle battute: `bars * barLen` e i colpi per battuta."""
    problems: List[str] = []
    checked = 0
    for index, event in enumerate(events):
        bar_len = event.get("barLen")
        bars = _bar_count(event)
        start = float(event.get("t0") or 0)
        end = float(event.get("t1") or 0)
        if bar_len is None or bars is None:
            continue
        try:
            bar_len = float(bar_len)
        except (TypeError, ValueError):
            problems.append(f"events[{index}]: barLen non numerico")
            continue
        checked += 1
        want = bars * bar_len
        # Mezza battuta di tolleranza: la pick-up prima del battere 1 e' musica,
        # non un errore. Oltre, la finestra non e' piu' quella che dichiara.
        if abs((end - start) - want) > max(BAR_SLACK * bar_len, 2 * tol):
            problems.append(
                f"events[{index}]: {bars} battute x {bar_len:.4f} = {want:.3f} s, "
                f"la finestra dura {end - start:.3f} s"
            )
        per_bar = event.get("hitsPerBar")
        hits = event.get("hits")
        if per_bar is None or not isinstance(hits, list):
            continue
        try:
            per_bar = int(per_bar)
            values = [float(value) for value in hits]
        except (TypeError, ValueError):
            continue
        for bar in range(bars):
            low = start + bar * bar_len - tol
            high = start + (bar + 1) * bar_len + (tol if bar == bars - 1 else -tol)
            count = sum(1 for value in values if low <= value <= high)
            if count != per_bar:
                problems.append(
                    f"events[{index}]: battuta {bar + 1} con {count} colpi invece di {per_bar}"
                )
                break
    if not checked:
        return [_skip("L9", "le battute tornano con i colpi dichiarati", "nessun event con barLen/bars")]
    return [
        _ok("L9", "le battute tornano con i colpi dichiarati", f"{checked} finestre")
        if not problems
        else _fail("L9", "le battute tornano con i colpi dichiarati", "; ".join(problems[:3]))
    ]


def _check_text(
    events: Sequence[Mapping[str, Any]], source: str
) -> List[Dict[str, Any]]:
    """Il confronto con il testo scritto: le parole della spec, in ordine.

    Non e' un confronto contiguo: la spec puo' **saltare** parole (l'artista ha
    deciso che l'"e'" cantato non si scrive) ma non puo' **sostituirle**. Si
    avanza con un cursore nel testo: una parola che non compare piu' dopo
    l'ultima trovata e' un errore, e si nomina con la parola e il punto.
    """
    written: List[Tuple[str, int, int]] = []
    for index, event in enumerate(events):
        for position, line in enumerate(event.get("lines") or []):
            for word in words(_line_text(line)):
                written.append((word, index, position))
    song = words(source)
    if not written:
        return [_skip("L10", "il testo combacia con quello scritto", "nessuna riga")]
    if not song:
        return [_skip("L10", "il testo combacia con quello scritto", "testo vuoto")]

    cursor = 0
    skipped = 0
    matched = 0
    last = ""
    for position, (word, event_index, line_index) in enumerate(written):
        found = -1
        for index in range(cursor, len(song)):
            if song[index] == word:
                found = index
                break
        if found < 0:
            after = f"dopo '{last}'" if last else "all'inizio"
            return [
                _fail(
                    "L10",
                    "il testo combacia con quello scritto",
                    f"parola {position + 1}: la spec dice '{word}', che nel testo "
                    f"non compare {after} (events[{event_index}].lines[{line_index}])",
                )
            ]
        skipped += found - cursor
        cursor = found + 1
        matched += 1
        last = word
    return [
        _ok(
            "L10",
            "il testo combacia con quello scritto",
            f"{matched} parole in ordine, {skipped} saltate nel testo",
        )
    ]


def _check_space(
    events: Sequence[Mapping[str, Any]], canvas: Optional[Tuple[int, int]]
) -> List[Dict[str, Any]]:
    if canvas is None:
        return [
            _skip("L11", "le righe stanno nel quadro e fuori dalla zona bassa", "nessun canvas")
        ]
    width, height = canvas
    problems: List[str] = []
    for index, event in enumerate(events):
        # `fitSize` e' la taglia che il template applica quando la riga non ci
        # sta: e' quella che si legge, non `size`.
        size = event.get("fitSize", event.get("size"))
        if isinstance(size, (int, float)) and size > 0:
            for position, line in enumerate(event.get("lines") or []):
                text = _line_text(line)
                estimate = len(text) * GLYPH_EM * float(size)
                if estimate > LINE_MARGIN * width:
                    problems.append(
                        f"events[{index}].lines[{position}]: ~{estimate:.0f} px su "
                        f"{LINE_MARGIN * width:.0f} disponibili"
                    )
        y = event.get("y")
        if isinstance(y, (int, float)):
            if not (0.02 * height <= float(y) <= 0.90 * height):
                problems.append(f"events[{index}]: y={y} fuori dal quadro")
    if not problems:
        return [
            _ok("L11", "le righe stanno nel quadro e fuori dalla zona bassa", "stima sui caratteri")
        ]
    return [
        _warn("L11", "le righe stanno nel quadro e fuori dalla zona bassa", "; ".join(problems[:3]))
    ]


def _check_master(
    spec: Mapping[str, Any], master: Path
) -> List[Dict[str, Any]]:
    from . import audio, probe as probe_mod

    rows: List[Dict[str, Any]] = []
    try:
        info = probe_mod.probe(master, count_frames=True).to_dict()
    except Exception as exc:  # noqa: BLE001 - illeggibile e' un no secco
        return [_fail("L12", "il master dura quello che dichiara", f"{type(exc).__name__}: {exc}")]
    duration = float(info.get("duration") or 0)
    frames = info.get("nb_frames")
    if frames is None:
        frames = info.get("frames")
    want_duration = float(spec.get("duration") or 0)
    fps = float(spec.get("fps") or 0)
    if want_duration and fps:
        tol = 1.0 / fps + 1e-6
        problems = []
        if abs(duration - want_duration) > tol:
            problems.append(f"{duration:.3f} s invece di {want_duration:.3f} s")
        want_frames = int(round(want_duration * fps))
        if isinstance(frames, int) and abs(frames - want_frames) > 1:
            problems.append(f"{frames} fotogrammi invece di {want_frames}")
        rows.append(
            _ok(
                "L12",
                "il master dura quello che dichiara",
                f"{duration:.3f} s / {frames if frames is not None else '?'} frame",
            )
            if not problems
            else _fail("L12", "il master dura quello che dichiara", "; ".join(problems))
        )
    else:
        rows.append(_skip("L12", "il master dura quello che dichiara", "spec senza durata/fps"))

    measured = audio.loudness(master)
    level = float(measured.get("input_i") or 0.0)
    peak = float(measured.get("input_tp") or 0.0)
    problems = []
    if abs(level - TARGET_LUFS) > LUFS_TOLERANCE:
        problems.append(f"{level:.2f} LUFS (target {TARGET_LUFS:g} +/- {LUFS_TOLERANCE:g})")
    if peak > TRUE_PEAK_CEILING:
        problems.append(f"true peak {peak:.2f} dBTP oltre {TRUE_PEAK_CEILING:g}")
    rows.append(
        _ok("L13", "l'audio e' al target", f"{level:.2f} LUFS / {peak:.2f} dBTP")
        if not problems
        else _fail("L13", "l'audio e' al target", "; ".join(problems))
    )

    # Il loop: ultimo fotogramma contro il primo.
    if fps <= 0 or duration <= 0:
        rows.append(_skip("L14", "il loop chiude a filo", "master senza fps/durata"))
        return rows
    try:
        first = _frame(master, 0.0, info)
        last = _frame(master, max(0.0, duration - 1.0 / fps), info)
        worst, share = _compare(first, last)
    except Exception as exc:  # noqa: BLE001 - un frame illeggibile e' un no
        rows.append(_fail("L14", "il loop chiude a filo", f"{type(exc).__name__}: {exc}"))
        return rows
    rows.append(
        _ok("L14", "il loop chiude a filo", f"delta={worst}/255, {share * 100:.5f}% dei pixel")
        if worst <= LOOP_DELTA and share <= LOOP_SHARE
        else _fail(
            "L14",
            "il loop chiude a filo",
            f"delta={worst}/255, {share * 100:.5f}% dei pixel",
        )
    )
    return rows


def _frame(path: Path, moment: float, info: Mapping[str, Any]) -> bytes:
    """Un fotogramma RGB grezzo al tempo dato, letto da ffmpeg.

    ``ffmpeg.run_ff`` decodifica l'output come testo, e dei pixel non ci si puo'
    fidare di un decode UTF-8: qui il processo parla binario e il resto
    dell'ambiente (binario dichiarato, argomenti, timeout) resta lo stesso.
    """
    import subprocess  # noqa: PLC0415 - solo per i due fotogrammi del loop

    from . import ffmpeg

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("dimensioni sconosciute")
    args = [
        ffmpeg.ffmpeg(),
        "-v",
        "error",
        "-ss",
        f"{max(0.0, float(moment)):.6f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    done = subprocess.run(args, capture_output=True, timeout=120, check=False)
    if done.returncode != 0 or len(done.stdout) < width * height * 3:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise ValueError(" ".join(detail) or "nessun fotogramma")
    return done.stdout[: width * height * 3]


def _compare(left: bytes, right: bytes) -> Tuple[int, float]:
    import numpy as np  # noqa: PLC0415 - solo per i due fotogrammi del loop

    a = np.frombuffer(left, dtype=np.uint8)
    b = np.frombuffer(right, dtype=np.uint8)
    if a.shape != b.shape:
        raise ValueError("i due fotogrammi hanno dimensioni diverse")
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16))
    worst = int(delta.max()) if delta.size else 0
    pixels = delta.reshape(-1, 3).max(axis=1)
    share = float((pixels > LOOP_DELTA).mean()) if pixels.size else 0.0
    return worst, share


# ------------------------------------------------------------------ ingresso


def check(
    spec: Mapping[str, Any],
    *,
    text: Optional[str] = None,
    text_note: str = "",
    master: Optional[str | Path] = None,
    canvas: Optional[Tuple[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Tutti i controlli, nell'ordine del report."""
    rows: List[Dict[str, Any]] = []
    rows.extend(_check_document(spec))
    windows_rows, events, duration, tol = _check_windows(spec)
    rows.extend(windows_rows)
    rows.extend(_check_coverage(spec, events, duration, tol))
    rows.extend(_check_lines(events))
    rows.extend(_check_hits(events, duration, tol))
    rows.extend(_check_bars(events, tol))
    if text is None:
        rows.append(_skip("L10", "il testo combacia con quello scritto", "nessun --text"))
        rows.append(_skip("L15", "il testo scritto si legge", "nessun --text"))
    else:
        rows.append(
            _warn("L15", "il testo scritto si legge", text_note)
            if text_note
            else _ok("L15", "il testo scritto si legge", "codifica pulita")
        )
        rows.extend(_check_text(events, text))
    size = canvas
    if size is None and isinstance(spec.get("size"), (list, tuple)) and len(spec["size"]) == 2:
        size = (int(spec["size"][0]), int(spec["size"][1]))
    rows.extend(_check_space(events, size))
    if master is None:
        rows.append(_skip("L12", "il master dura quello che dichiara", "nessun --master"))
        rows.append(_skip("L13", "l'audio e' al target", "nessun --master"))
        rows.append(_skip("L14", "il loop chiude a filo", "nessun --master"))
    else:
        rows.extend(_check_master(spec, Path(master)))
    return rows


def summarise(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Il conteggio onesto: passati, avvertimenti, falliti, dichiarati."""
    rows = list(rows)
    counts = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for row in rows:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
    return {
        "ok": counts["fail"] == 0,
        "counts": counts,
        "failed": [row["id"] for row in rows if row.get("status") == "fail"],
        "skipped": [row["id"] for row in rows if row.get("status") == "skip"],
        "checks": rows,
    }
