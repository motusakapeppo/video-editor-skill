"""vx.sections — il piano-scaletta: sezioni compilate con le misure, non prosa.

Il piano non si approva e non lo scrive nessuno: si **compila** da quello che il
lavoro ha gia' misurato. Ogni sezione dichiara quattro cose:

- ``capability``: quale competenza della skill l'ha prodotta (una che esiste;
  il test verifica che non ci siano orfani);
- ``binding``: il materiale vero (clip, secondi, in/out) su cui poggia;
- ``why``: il numero misurato che la giustifica;
- ``state``: ``filled`` (tutte e tre), ``missing`` (la capacita' c'e' ma la
  misura no), ``absent`` (la competenza non e' stata chiamata), ``dropped``
  (richiesta ma impossibile, col motivo).

Una sezione senza numero non e' compilata: finisce in ``missing`` o ``dropped``.
Le competenze non si spengono: questo modulo **legge** il report e non decide
niente, quindi non puo' togliere una chiamata a nessuno.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import contract

__all__ = [
    "SECTIONS",
    "CAPABILITIES",
    "COMPOSITION_SCHEMA",
    "compile_sections",
    "compose",
    "describe",
    "measure_application",
    "measure_survival",
]

#: La composizione non è un piano nuovo: è la stessa scaletta che dice *quanto*
#: durano le sezioni. Lo schema è chiuso, così una modifica si vede nel diff.
COMPOSITION_SCHEMA = "vx.composition/1"

#: Le sezioni dello scheletro, in ordine di lettura. Chiuse: non se ne inventano.
SECTIONS = ("hook", "beat", "payoff", "cta", "proof", "broll")

#: Le competenze che una sezione puo' citare. Se una non e' qui, e' un errore.
CAPABILITIES = (
    "typography",
    "decide",
    "bridge",
    "coherence",
    "efx",
    "faces",
    "motion",
    "subs",
    "audio",
    "render",
)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _limit(name: str, default: float) -> float:
    """Una soglia della composizione: il valore dichiarato, non un numero sparso."""
    value = contract.THRESHOLDS.get(name)
    number = _number(value)
    return float(default) if number is None else number


def _quantize(seconds: float, fps: float) -> float:
    """Il taglio cade su un frame: la somma delle due parti resta quella di prima."""
    if fps and fps > 0:
        return round(round(float(seconds) * float(fps)) / float(fps), 6)
    return round(float(seconds), 6)


def _runs_of(row: Mapping[str, Any]) -> List[Dict[str, float]]:
    runs: List[Dict[str, float]] = []
    raw = row.get("usable_runs")
    if not isinstance(raw, (list, tuple)):
        return runs
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None or end is None or end - start <= 1e-9:
            continue
        runs.append({"start": start, "end": end})
    return runs


def _opening_row(
    opening: Mapping[str, Any], a_roll: Sequence[Mapping[str, Any]]
) -> Optional[Mapping[str, Any]]:
    asset = str(opening.get("asset_id") or "")
    return next(
        (item for item in a_roll if str(item.get("asset_id") or "") == asset),
        None,
    )


def _opening_run(
    opening: Mapping[str, Any], a_roll: Sequence[Mapping[str, Any]]
) -> Optional[Dict[str, float]]:
    """La corsa utile che contiene il punto di presa dell'apertura."""
    row = _opening_row(opening, a_roll)
    if row is None:
        return None
    runs = _runs_of(row)
    if not runs:
        return None
    point = _number(row.get("src_in"))
    if point is None:
        point = _number(opening.get("src_in")) or 0.0
    for run in runs:
        if run["start"] - 1e-9 <= point <= run["end"] + 1e-9:
            return dict(run)
    return runs[0]


def _hook_take_point(
    opening: Mapping[str, Any],
    a_roll: Sequence[Mapping[str, Any]],
    seconds: float,
    tail: float,
) -> tuple[Optional[float], Dict[str, Any], Optional[str]]:
    """Dove deve entrare il gancio perché duri ``seconds`` e resti il corpo.

    Il punto di presa scelto dal probe è il picco di nitidezza e lascia solo
    ``min_keep`` secondi dopo di sé: spesso meno dell'hook. Qui, **solo** quando
    serve, si sposta il punto all'indietro (mai avanti) dentro la stessa corsa
    utile, scegliendo il campione più vicino al picco che:
    - lascia ``seconds + tail`` secondi di materiale dopo di sé,
    - sta sopra la soglia *preferita* già misurata per quella clip.
    Se un campione così non esiste, la leva declina coi numeri: non si allunga
    un gancio tagliando dentro il materiale che non c'è.
    """
    row = _opening_row(opening, a_roll)
    if row is None:
        return None, {}, "apertura non presente nella lista degli a_roll"
    runs = _runs_of(row)
    if not runs:
        return None, {}, "nessuna corsa utile misurata per l'apertura"
    point = _number(row.get("src_in"))
    if point is None:
        point = _number(opening.get("src_in"))
    if point is None:
        return None, {}, "punto di presa non misurato: niente da spostare"
    run = next(
        (
            item
            for item in runs
            if item["start"] - 1e-9 <= point <= item["end"] + 1e-9
        ),
        runs[0],
    )
    need = seconds + tail
    if run["end"] - point >= need - 1e-9:
        return point, {"from": round(point, 3), "to": round(point, 3), "moved": False}, None
    samples = [
        item
        for item in row.get("samples") or ()
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    preferred = _number(row.get("preferred"))
    if not samples or preferred is None:
        return (
            None,
            {"room": round(run["end"] - point, 3), "need": round(need, 3)},
            (
                f"la corsa dell'apertura lascia {run['end'] - point:.2f} s dopo il "
                f"punto di presa e servono {need:.2f} s per un hook di "
                f"{seconds:.2f} s; profilo o soglia preferita non disponibili per "
                "cercare un punto migliore"
            ),
        )
    limit = run["end"] - need
    if limit < run["start"] - 1e-9:
        return (
            None,
            {
                "room": round(run["end"] - run["start"], 3),
                "need": round(need, 3),
            },
            (
                "la corsa utile dell'apertura è "
                f"{run['end'] - run['start']:.2f} s: non resta spazio per un gancio "
                f"di {seconds:.2f} s più la coda di {tail:.2f} s"
            ),
        )
    candidates = [
        (float(t), float(value))
        for t, value in samples
        if run["start"] - 1e-9 <= float(t) <= limit + 1e-9
        and float(value) >= preferred - 1e-9
    ]
    if not candidates:
        best = max((float(value) for t, value in samples if t <= limit + 1e-9), default=None)
        return (
            None,
            {
                "room": round(run["end"] - point, 3),
                "need": round(need, 3),
                "preferred": round(preferred, 2),
            },
            (
                f"la corsa dell'apertura lascia {run['end'] - point:.2f} s dopo il "
                f"punto di presa e servono {need:.2f} s: nessun campione prima di "
                f"{limit:.2f} s sta sopra la soglia preferita {preferred:.2f}"
                + (f" (migliore {best:.1f})" if best is not None else "")
            ),
        )
    at, sharpness = min(candidates, key=lambda item: (abs(item[0] - point), -item[1]))
    return (
        at,
        {
            "from": round(point, 3),
            "to": round(at, 3),
            "moved": True,
            "sharpness": round(sharpness, 1),
            "preferred": round(preferred, 2),
        },
        None,
    )


def _hook_proposal(
    opening: Mapping[str, Any],
    a_roll: Sequence[Mapping[str, Any]],
    beat: Mapping[str, Any],
    duration: float,
    fps: float,
) -> tuple[Optional[float], Dict[str, Any], Optional[str]]:
    """Quanto dura il gancio, con i numeri che lo giustificano.

    Tre limiti, tutti misurati: quanto è nitida l'apertura rispetto alla soglia
    del triage (un'apertura forte regge più a lungo), quanto materiale utile
    resta nella sua corsa, quanto può occupare della durata chiesta. Con una
    griglia BPM misurata la leva declina: lì i tagli li decide il tempo, e
    fingere un hook «composto» sopra la griglia sarebbe una seconda verità.
    """
    mins = _limit("composition_hook_min_sec", 1.0)
    base_default = _limit("composition_hook_base_sec", 2.0)
    strong = _limit("composition_hook_strong_sec", 2.4)
    weak = _limit("composition_hook_weak_sec", 1.2)
    ceiling = _limit("composition_hook_max_sec", 3.0)
    share_max = _limit("composition_hook_share_max", 0.35)
    tail = _limit("composition_hook_tail_sec", 0.5)
    strong_ratio = _limit("composition_hook_strong_ratio", 1.5)
    weak_ratio = _limit("composition_hook_weak_ratio", 1.0)

    asset = str(opening.get("asset_id") or "")
    if not asset:
        return None, {}, "nessuna apertura scelta: niente da comporre"

    bpm = _number(beat.get("bpm")) or 0.0
    if bool(beat.get("enabled")) and bpm > 0:
        try:
            subdivision = int(beat.get("subdivision") or 1)
        except (TypeError, ValueError):
            subdivision = 1
        if subdivision not in (1, 2, 4):
            subdivision = 1
        step = 60.0 / bpm / subdivision
        step = _quantize(step, fps)
        if step <= 0:
            return None, {}, "griglia accesa ma passo non calcolabile"
        return (
            step,
            {
                "source": "beat_grid",
                "bpm": round(bpm, 3),
                "subdivision": subdivision,
                "step": round(step, 4),
                "why": (
                    f"griglia {bpm:.2f} BPM (1/{subdivision}): i tagli cadono ogni "
                    f"{step:.2f} s, quindi il gancio è il primo shot"
                ),
            },
            None,
        )

    sharp = _number(opening.get("sharpness"))
    threshold = _number(opening.get("threshold"))
    ratio: Optional[float] = None
    if sharp is not None and threshold is not None and threshold > 0:
        ratio = sharp / threshold
    if ratio is None:
        base = base_default
        base_why = f"nitidezza non misurata: base prudente {base:.1f} s"
    elif ratio < weak_ratio:
        base = weak
        base_why = (
            f"apertura sotto soglia: nitidezza {sharp:.1f} contro {threshold:.1f} "
            f"({ratio:.2f}x), si taglia via prima ({base:.1f} s)"
        )
    elif ratio < strong_ratio:
        base = base_default
        base_why = (
            f"apertura nella norma: nitidezza {sharp:.1f} contro {threshold:.1f} "
            f"({ratio:.2f}x) -> {base:.1f} s"
        )
    else:
        base = strong
        base_why = (
            f"apertura nitida: nitidezza {sharp:.1f} contro {threshold:.1f} "
            f"({ratio:.2f}x) -> {base:.1f} s"
        )

    seconds = base
    beat_rule: Optional[Dict[str, Any]] = None
    beat_sec = 60.0 / bpm if bpm > 0 else 0.0
    if beat_sec > 0:
        beats = max(2, int(round(base / beat_sec)))
        snapped = beats * beat_sec
        while snapped > ceiling + 1e-9 and beats > 2:
            beats -= 1
            snapped = beats * beat_sec
        if mins - 1e-9 <= snapped <= ceiling + 1e-9:
            seconds = snapped
            beat_rule = {"bpm": round(bpm, 3), "beats": beats, "seconds": round(snapped, 3)}
        else:
            beat_rule = {"bpm": round(bpm, 3), "beats": None, "seconds": None}

    share_limit: Optional[float] = None
    if duration > 0:
        share_limit = max(mins, duration * share_max)
        seconds = min(seconds, share_limit)

    seconds = min(seconds, ceiling)
    seconds = max(seconds, mins)
    seconds = _quantize(seconds, fps)
    if seconds < mins - 1e-9 or seconds > ceiling + 1e-9:
        return (
            None,
            {"base": round(base, 3), "ratio": None if ratio is None else round(ratio, 3)},
            f"la durata calcolata ({seconds:.3f} s) esce dai limiti [{mins:.1f}, {ceiling:.1f}] s",
        )

    take_point, take_rule, take_declined = _hook_take_point(
        opening, a_roll, seconds, tail
    )
    if take_point is None:
        return (
            None,
            {
                "base": round(base, 3),
                "ratio": None if ratio is None else round(ratio, 3),
                "seconds": seconds,
                "take": take_rule,
            },
            str(take_declined or "nessun punto di presa utile per il gancio"),
        )

    run = _opening_run(opening, a_roll)
    material_limit = None if run is None else run["end"] - take_point - tail
    parts = [base_why]
    if str(opening.get("measure") or "") == "profile":
        parts.insert(
            0,
            "apertura misurata dal profilo per-frame (il triage non era affidabile)",
        )
    if beat_rule and beat_rule.get("beats"):
        parts.append(
            f"griglia {bpm:.2f} BPM: {beat_rule['beats']} battute = {seconds:.2f} s"
        )
    if share_limit is not None and seconds <= share_limit + 1e-9 and seconds < base - 1e-9:
        parts.append(f"limite di quota {share_limit:.2f} s su {duration:.2f} s chiesti")
    if take_rule.get("moved"):
        parts.append(
            "punto di presa spostato da {before:.2f} s a {after:.2f} s "
            "(nitidezza {sharpness:.1f} ≥ soglia preferita {preferred:.2f}) "
            "per dare al gancio il suo tempo".format(
                before=float(take_rule.get("from") or 0.0),
                after=float(take_rule.get("to") or 0.0),
                sharpness=float(take_rule.get("sharpness") or 0.0),
                preferred=float(take_rule.get("preferred") or 0.0),
            )
        )
    parts.append(f"quantizzato al frame a {fps:g} fps")
    rule = {
        "asset_id": asset,
        "sharpness": None if sharp is None else round(sharp, 1),
        "threshold": None if threshold is None else round(threshold, 1),
        "ratio": None if ratio is None else round(ratio, 2),
        "base": round(base, 3),
        "beat": beat_rule,
        "take": take_rule,
        "limits": {
            "share": None if share_limit is None else round(share_limit, 3),
            "material": None if material_limit is None else round(material_limit, 3),
        },
        "seconds": seconds,
        "why": "; ".join(parts),
    }
    return seconds, rule, None


def _broll_proposal(
    b_roll: Sequence[Mapping[str, Any]]
) -> tuple[Optional[List[str]], Dict[str, Any], Optional[str]]:
    """L'ordine dei b-roll per nitidezza misurata, o nessun ordine inventato."""
    ids = [str(entry.get("asset_id") or "") for entry in b_roll]
    if not ids:
        return None, {}, "nessun b-roll nel materiale: non c'è un ordine da comporre"
    if len(ids) < 2:
        return None, {}, f"un solo b-roll ({ids[0]}): nessun ordine da cambiare"
    measured: Dict[str, float] = {}
    for entry in b_roll:
        asset = str(entry.get("asset_id") or "")
        value = _number(entry.get("sharpness"))
        if asset and value is not None:
            measured[asset] = value
    if len(measured) < 2:
        return (
            None,
            {"measured": len(measured), "total": len(ids)},
            f"nitidezza misurata su {len(measured)} b-roll di {len(ids)}: "
            "senza due misure non c'è un ordine da giustificare",
        )
    index = {asset: position for position, asset in enumerate(ids)}
    order = sorted(
        ids,
        key=lambda asset: (
            asset not in measured,
            -measured.get(asset, 0.0),
            index.get(asset, 0),
        ),
    )
    if order == ids:
        best = sorted(measured.items(), key=lambda item: (-item[1], item[0]))
        return (
            None,
            {"measured": measured},
            "l'ordine misurato coincide con quello del payload ("
            + ", ".join(f"{asset} {value:.1f}" for asset, value in best)
            + ")",
        )
    best = sorted(measured.items(), key=lambda item: (-item[1], item[0]))
    return (
        order,
        {
            "measured": {asset: round(value, 1) for asset, value in measured.items()},
            "why": "b-roll per nitidezza misurata: "
            + " > ".join(f"{asset} {value:.1f}" for asset, value in best),
        },
        None,
    )


def compose(facts: Any) -> Dict[str, Any]:
    """La composizione proposta dalle misure: pura, deterministica, senza LLM.

    Legge solo ciò che il lavoro ha già misurato **prima** del piano (nitidezza
    dell'apertura e delle clip, corse utili del probe, BPM della traccia,
    durata chiesta) e propone tre cose: quanto dura l'hook, in che ordine
    entrano i b-roll. Il numero di inquadrature non si propone: si **misura**
    sui due piani (vedi :func:`measure_application`), perché una promessa non è
    una prova. Quando una misura manca la leva declina col motivo.
    """
    data = facts if isinstance(facts, Mapping) else {}
    fps = _number(data.get("fps")) or 0.0
    duration = _number(data.get("duration_sec")) or 0.0
    opening = data.get("opening") if isinstance(data.get("opening"), Mapping) else {}
    a_roll = [
        item for item in data.get("a_roll") or [] if isinstance(item, Mapping)
    ]
    b_roll = [
        item for item in data.get("b_roll") or [] if isinstance(item, Mapping)
    ]
    beat = data.get("beat") if isinstance(data.get("beat"), Mapping) else {}

    proposed: Dict[str, Any] = {}
    rules: Dict[str, Any] = {}
    declined: List[Dict[str, str]] = []

    hook_seconds, hook_rule, hook_declined = _hook_proposal(
        opening, a_roll, beat, duration, fps
    )
    if hook_seconds is not None:
        proposed["hook_seconds"] = hook_seconds
        proposed["hook_asset"] = str(opening.get("asset_id") or "")
        rules["hook"] = hook_rule
    else:
        rules["hook"] = hook_rule
        declined.append({"id": "hook", "why": str(hook_declined or "non applicabile")})

    order, broll_rule, broll_declined = _broll_proposal(b_roll)
    if order is not None:
        proposed["broll_order"] = order
        rules["broll"] = broll_rule
    else:
        rules["broll"] = broll_rule
        declined.append({"id": "broll", "why": str(broll_declined or "non applicabile")})
    if b_roll:
        # La convenzione non è un numero: un overlay che entra su un taglio non
        # sembra un errore di montaggio. Il builder conosce i tagli veri e sposta
        # solo se ce n'è uno entro la tolleranza; qui si chiede la politica.
        proposed["broll_placement"] = "nearest_cut"
        rules.setdefault("broll", {})["placement"] = {
            "policy": "nearest_cut",
            "why": (
                "i b-roll entrano sul taglio più vicino allo slot uniforme: "
                "un taglio misurabile, non una posizione qualunque"
            ),
        }

    # La coda: quando la richiesta nomina una CTA (o una testimonianza), gli
    # ultimi secondi non si coprono con un b-roll che sfuma sopra il messaggio.
    wanted = data.get("expectations")
    wanted = wanted if isinstance(wanted, Mapping) else {}
    tail = 0.0
    if wanted.get("cta") or wanted.get("proof"):
        tail = _limit("composition_tail_sec", 1.5)
        proposed["tail_seconds"] = round(tail, 3)
        rules["tail"] = {
            "seconds": round(tail, 3),
            "why": (
                "la richiesta chiede una "
                + ("cta" if wanted.get("cta") else "testimonianza")
                + f": gli ultimi {tail:.2f} s restano liberi da b-roll"
            ),
        }

    parts: List[str] = []
    if "hook_seconds" in proposed:
        parts.append(f"hook {proposed['hook_seconds']:.2f} s su {proposed['hook_asset']}")
    if "broll_order" in proposed:
        parts.append("b-roll in ordine " + ", ".join(proposed["broll_order"]))
    if "broll_placement" in proposed:
        parts.append("b-roll sui tagli")
    if tail:
        parts.append(f"coda {tail:.2f} s libera")
    return {
        "schema": COMPOSITION_SCHEMA,
        "proposed": proposed,
        "rules": rules,
        "declined": declined,
        "why": "; ".join(parts) if parts else "nessuna leva componibile: misure assenti",
    }


def _clips(plan: Any, layer: int) -> List[Any]:
    """I clip video di un layer, nell'ordine della traccia."""
    out: List[Any] = []
    tracks = getattr(plan, "video_tracks", ()) or ()
    for track in tracks:
        for clip in getattr(track, "clips", ()) or ():
            if int(getattr(clip, "layer", 0) or 0) == layer:
                out.append(clip)
    return out


def measure_application(
    composition: Any, baseline: Any, plan: Any
) -> Dict[str, Any]:
    """Cosa la composizione ha **davvero** cambiato, misurato sui due piani.

    Le inquadrature sono contate, l'hook è la durata del primo clip del piano
    composto, gli slot dei b-roll sono i loro ingressi. Se la composizione non
    è stata applicata i numeri coincidono e il blocco lo dice: nessuna riga del
    report afferma qualcosa che il piano non mostra.
    """
    block = dict(composition) if isinstance(composition, Mapping) else {}
    proposed = block.get("proposed") if isinstance(block.get("proposed"), Mapping) else {}
    fps = _number(getattr(plan, "fps", None)) or _number(getattr(baseline, "fps", None)) or 0.0
    tolerance = (1.0 / fps + 1e-6) if fps else 0.002

    before = _clips(baseline, 0)
    after = _clips(plan, 0)
    hook_after = _number(after[0].duration) if after else None
    hook_before = _number(before[0].duration) if before else None
    proposed_hook = _number(proposed.get("hook_seconds"))
    applied_hook = (
        proposed_hook is not None
        and hook_after is not None
        and abs(hook_after - proposed_hook) <= tolerance
    )
    slots_before = [round(float(clip.dst_in), 3) for clip in _clips(baseline, 1)]
    slots_after = [round(float(clip.dst_in), 3) for clip in _clips(plan, 1)]
    order_before = [str(clip.source_id) for clip in _clips(baseline, 1)]
    order_after = [str(clip.source_id) for clip in _clips(plan, 1)]
    tail = _number(proposed.get("tail_seconds")) or 0.0
    broll_after = _clips(plan, 1)
    last_out = max((float(clip.dst_out) for clip in broll_after), default=None)
    respects_tail = True if last_out is None or tail <= 0 else (
        last_out <= float(plan.duration_sec) + tolerance - tail
    )
    rules = block.get("rules") if isinstance(block.get("rules"), Mapping) else {}
    hook_rules = rules.get("hook") if isinstance(rules.get("hook"), Mapping) else {}
    take = hook_rules.get("take") if isinstance(hook_rules.get("take"), Mapping) else {}
    take_to = _number(take.get("to"))
    src_in = _number(after[0].src_in) if after else None
    take_moved = bool(take.get("moved")) and take_to is not None
    take_honored = (
        not take_moved
        or (src_in is not None and abs(src_in - take_to) <= tolerance)
    )
    block["applied"] = {
        "hook_seconds": hook_after if hook_after is None else round(hook_after, 3),
        "hook_baseline_seconds": hook_before if hook_before is None else round(hook_before, 3),
        "hook_applied": bool(applied_hook),
        "hook_asset": str(proposed.get("hook_asset") or "") or None,
        "hook_src_in": None if src_in is None else round(src_in, 3),
        "hook_take_from": None if not take_moved else _number(take.get("from")),
        "hook_take_to": None if not take_moved else take_to,
        "hook_take_moved": take_moved,
        "hook_take_honored": take_honored if take_moved else None,
        "shots_before": len(before),
        "shots_after": len(after),
        "delta": len(after) - len(before),
        "broll_slots_before": slots_before,
        "broll_slots_after": slots_after,
        "broll_order_before": order_before,
        "broll_order_after": order_after,
        "broll_order_changed": order_before != order_after,
        "broll_moved": slots_before != slots_after,
        "broll_moved_count": sum(
            1
            for old, new in zip(slots_before, slots_after)
            if abs(old - new) > 1e-6
        ),
        "broll_placement": str(proposed.get("broll_placement") or "") or None,
        "tail_sec": round(tail, 3),
        "broll_last_out": None if last_out is None else round(last_out, 3),
        "broll_respects_tail": bool(respects_tail),
        "survived": bool(applied_hook) if proposed_hook is not None else True,
    }
    return block


def measure_survival(composition: Any, plan: Any) -> Dict[str, Any]:
    """Il gancio composto è ancora nel **piano finale**?

    La composizione si misura quando il piano nasce; dopo `plan_choice` e
    `creative` il piano può cambiare (uno scambio, un accorciamento). Qui si
    ricontrolla sul piano che si rende davvero: se il primo clip non dura più
    l'hook proposto, il report lo dice invece di lasciar credere che sia ancora
    in testa.
    """
    block = composition if isinstance(composition, Mapping) else {}
    proposed = block.get("proposed") if isinstance(block.get("proposed"), Mapping) else {}
    expected = _number(proposed.get("hook_seconds"))
    clips = _clips(plan, 0)
    first = _number(clips[0].duration) if clips else None
    fps = _number(getattr(plan, "fps", None)) or 0.0
    tolerance = (1.0 / fps + 1e-6) if fps else 0.002
    if expected is None:
        return {
            "hook_seconds": first,
            "expected": None,
            "preserved": True,
            "shots_final": len(clips),
            "why": "nessun gancio proposto: niente da preservare",
        }
    preserved = first is not None and abs(first - expected) <= tolerance
    return {
        "hook_seconds": None if first is None else round(first, 3),
        "expected": round(expected, 3),
        "preserved": bool(preserved),
        "shots_final": len(clips),
        "why": (
            f"il primo clip dura {first:.3f}s come l'hook proposto"
            if preserved
            else (
                "il piano finale apre con "
                + ("nessun clip" if first is None else f"{first:.3f}s")
                + f" invece dell'hook di {expected:.3f}s"
            )
        ),
    }


def describe(composition: Any) -> str:
    """Una riga in italiano: cosa ha composto il piano e con quali numeri."""
    block = composition if isinstance(composition, Mapping) else {}
    proposed = block.get("proposed") if isinstance(block.get("proposed"), Mapping) else {}
    rules = block.get("rules") if isinstance(block.get("rules"), Mapping) else {}
    applied = block.get("applied") if isinstance(block.get("applied"), Mapping) else {}
    declined = {
        str(item.get("id")): str(item.get("why"))
        for item in block.get("declined") or ()
        if isinstance(item, Mapping)
    }
    parts: List[str] = []

    hook_rule = rules.get("hook") if isinstance(rules.get("hook"), Mapping) else {}
    if proposed.get("hook_seconds") is not None:
        if applied and not applied.get("hook_applied"):
            parts.append(
                "hook proposto "
                f"{float(proposed['hook_seconds']):.2f} s ma non applicato"
            )
        else:
            detail = str(hook_rule.get("why") or "")
            note = ""
            if applied.get("hook_take_moved") and applied.get("hook_take_honored") is False:
                detail = detail.split("; punto di presa spostato")[0]
                entered = applied.get("hook_src_in")
                if isinstance(entered, (int, float)):
                    note = (
                        f"; il piano usa la corsa intera: si entra da "
                        f"{float(entered):.2f} s, non da "
                        f"{float(applied.get('hook_take_to') or 0.0):.2f} s"
                    )
            measured = (
                f"; prima {applied['hook_baseline_seconds']:.2f} s"
                if applied.get("hook_baseline_seconds") is not None
                else ""
            )
            seconds = float(
                applied.get("hook_seconds") or proposed.get("hook_seconds") or 0.0
            )
            parts.append(f"hook {seconds:.2f} s{measured} ({detail}{note})")
    elif "hook" in declined:
        parts.append(f"hook declinato ({declined['hook']})")

    broll_rule = rules.get("broll") if isinstance(rules.get("broll"), Mapping) else {}
    if proposed.get("broll_order"):
        order = ", ".join(str(item) for item in proposed["broll_order"])
        detail = str(broll_rule.get("why") or "")
        parts.append(f"b-roll in ordine {order}" + (f" ({detail})" if detail else ""))
    elif "broll" in declined:
        parts.append(f"b-roll invariati ({declined['broll']})")
    moved = applied.get("broll_moved_count")
    if isinstance(moved, int) and moved > 0:
        parts.append(f"{moved} b-roll entrati su un taglio")
    if isinstance(applied.get("tail_sec"), (int, float)) and float(applied["tail_sec"]) > 0:
        parts.append(f"coda {float(applied['tail_sec']):.2f} s libera da b-roll")

    if applied:
        parts.append(
            f"clip {int(applied.get('shots_before') or 0)} -> "
            f"{int(applied.get('shots_after') or 0)}"
        )
    return "composizione: " + ("; ".join(parts) if parts else str(block.get("why") or "niente da comporre"))


def expectations(request: Any) -> Dict[str, Dict[str, Any]]:
    """Cosa la richiesta **pretende** dalla scaletta, prima di montare.

    Il prompt non è solo un testo: se dice "hook: ..." o "cta: ..." sta chiedendo
    una sezione. Qui la richiesta diventa un requisito verificabile, così una
    scaletta compilata a metà non passa per buona. Solo ciò che l'utente ha
    scritto diventa obbligatorio: niente requisiti inventati.
    """
    text = str(request or "").lower()
    wanted: Dict[str, Dict[str, Any]] = {}

    def require(section: str, needles: tuple, why: str) -> None:
        if any(needle in text for needle in needles):
            wanted[section] = {"required": True, "why": why}

    require("hook", ("hook:", "hook ", "aggancio"), "la richiesta nomina un hook")
    require("cta", ("cta:", "cta ", "call to action"), "la richiesta nomina una cta")
    require(
        "proof",
        ("testimoni", "intervista", "racconto", "documentario"),
        "la richiesta chiede una testimonianza",
    )
    return wanted


def audit(
    compiled: Mapping[str, Any], wanted: Mapping[str, Mapping[str, Any]]
) -> Dict[str, Any]:
    """Confronta ciò che la richiesta pretende con ciò che la scaletta ha compilato.

    Un requisito senza sezione compilata è un **buco**, non un dettaglio: diventa
    un check bloccante nel report, e la consegna si ferma. È la parte in cui la
    scaletta guida il lavoro invece di limitarsi a descriverlo.
    """
    rows = compiled.get("sections") if isinstance(compiled.get("sections"), list) else []
    states = {
        str(row.get("id")): str(row.get("state"))
        for row in rows
        if isinstance(row, Mapping)
    }
    gaps: List[Dict[str, Any]] = []
    for section, spec in (wanted or {}).items():
        if not isinstance(spec, Mapping) or not spec.get("required"):
            continue
        state = states.get(section, "absent")
        if state == "filled":
            continue
        gaps.append(
            {
                "section": section,
                "state": state,
                "why": (
                    f"la richiesta chiede {section} ({spec.get('why')}) ma la "
                    f"sezione e' {state}: il video non la contiene"
                ),
            }
        )
    checks = [
        {
            "id": f"section_{gap['section']}",
            "passed": False,
            "measured": {"state": gap["state"]},
            "expected": {"required": True},
            "why": gap["why"],
        }
        for gap in gaps
    ]
    return {"gaps": gaps, "checks": checks, "ok": not gaps}

def _block(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}


def compile_sections(report: Any) -> Dict[str, Any]:
    """Compila le sezioni dalle misure gia' presenti nel report.

    Non solleva e non inventa: un dato che manca produce ``missing`` col motivo.
    """
    data = report if isinstance(report, Mapping) else {}
    measured = _block(data, "measured")
    opening = _block(data, "opening")
    bridge = _block(data, "bridge")
    typography = _block(data, "typography")
    coherence = _block(data, "coherence")
    faces = _block(data, "faces")
    selected = data.get("selected") if isinstance(data.get("selected"), list) else []
    texts = data.get("texts") if isinstance(data.get("texts"), list) else []

    duration = _number(measured.get("duration_sec"))
    clips = _number(measured.get("clips"))
    rows: List[Dict[str, Any]] = []

    def add(
        section: str,
        capability: str,
        *,
        state: str,
        binding: Any,
        why: str,
    ) -> None:
        rows.append(
            {
                "id": section,
                "state": state,
                "capability": capability,
                "binding": binding,
                "why": why,
            }
        )

    # hook: il primo frammento, scelto per nitidezza misurata.
    chosen = opening.get("chosen") if isinstance(opening.get("chosen"), Mapping) else {}
    sharp = _number(chosen.get("sharpness"))
    if chosen and sharp is not None:
        add(
            "hook",
            "decide",
            state="filled",
            binding={
                "asset_id": chosen.get("asset_id"),
                "seconds": round(min(2.5, duration or 0.0), 3),
            },
            why=f"apertura scelta per nitidezza {sharp:.1f} (soglia {opening.get('threshold')})",
        )
    elif clips:
        add(
            "hook",
            "decide",
            state="missing",
            binding=None,
            why="nessuna apertura misurata: il report non porta la nitidezza del primo frammento",
        )
    else:
        add(
            "hook",
            "decide",
            state="absent",
            binding=None,
            why="nessun materiale da aprire",
        )
    # L'hook può essere un **testo** (la richiesta lo chiede a parole) oppure
    # l'apertura scelta per nitidezza. Se il testo c'è, la sezione è compilata
    # anche quando la nitidezza non è stata misurata: prima un video con l'hook
    # scritto veniva bocciato per "manca l'hook".
    if texts and not any(row["id"] == "hook" and row["state"] == "filled" for row in rows):
        rows[-1] = {
            "id": "hook",
            "state": "filled",
            "capability": "typography",
            "binding": {"texts": 1, "characters": len(str(texts[0]))},
            "why": f"testo di apertura composto, {len(str(texts[0]))} caratteri",
        }

    # beat: il corpo, con la densita' di taglio che la coerenza ha misurato.
    shots = _number((coherence.get("metrics") or {}).get("shots")) if isinstance(
        coherence.get("metrics"), Mapping
    ) else None
    if shots and duration:
        add(
            "beat",
            "coherence",
            state="filled",
            binding={"shots": int(shots), "seconds": round(duration, 3)},
            why=f"{int(shots)} inquadrature in {duration:.2f}s, misurate da coherence",
        )
    elif clips:
        add(
            "beat",
            "coherence",
            state="missing",
            binding=None,
            why="nessuna misura di inquadrature: la coerenza non ha girato",
        )
    else:
        add(
            "beat",
            "coherence",
            state="absent",
            binding=None,
            why="nessun corpo da misurare",
        )

    # payoff: un picco misurato (qui: la fine del parlato utile, se c'e').
    cuts = bridge.get("cuts")
    if isinstance(cuts, (int, float)) and not isinstance(cuts, bool) and cuts > 0:
        add(
            "payoff",
            "bridge",
            state="filled",
            binding={"seconds": round(duration or 0.0, 3)},
            why=f"{int(cuts)} tagli sul ponte: la coda e' il punto di massima densita'",
        )
    elif duration:
        add(
            "payoff",
            "bridge",
            state="absent",
            binding=None,
            why="ponte senza tagli: nessun picco da dichiarare",
        )
    else:
        add(
            "payoff",
            "bridge",
            state="absent",
            binding=None,
            why="nessun ponte misurato",
        )

    # cta: esiste solo se il testo la chiede.
    if texts:
        add(
            "cta",
            "typography",
            state="filled",
            binding={"texts": len(texts)},
            why=f"{len(texts)} testi composti; l'ultimo e' la chiusura",
        )
    else:
        add(
            "cta",
            "typography",
            state="absent",
            binding=None,
            why="nessun testo richiesto",
        )

    # proof: serve un volto tracciato E parlato.
    speakers = faces.get("speakers") if isinstance(faces.get("speakers"), (int, float)) else None
    if speakers:
        add(
            "proof",
            "faces",
            state="filled",
            binding={"speakers": int(speakers)},
            why=f"{int(speakers)} volti tracciati",
        )
    else:
        add(
            "proof",
            "faces",
            state="absent",
            binding=None,
            why="nessun volto tracciato: la sezione non si accende",
        )

    # broll: solo se c'e' materiale non usato come a_roll.
    brolls = [
        entry
        for entry in selected
        if isinstance(entry, Mapping) and str(entry.get("role")) == "b_roll"
    ]
    if brolls:
        add(
            "broll",
            "decide",
            state="filled",
            binding={"assets": len(brolls)},
            why=f"{len(brolls)} asset in b-roll disponibili",
        )
    else:
        add(
            "broll",
            "decide",
            state="absent",
            binding=None,
            why="nessun b-roll: tutte le sorgenti sono in a_roll",
        )

    filled = [row for row in rows if row["state"] == "filled"]
    used = sorted({row["capability"] for row in rows if row["state"] == "filled"})
    unknown = sorted(set(used) - set(CAPABILITIES))
    # La composizione, se c'è, si vede **nella sezione che ha toccato**: la
    # durata vera del gancio sul piano, non quella proposta. Un blocco senza
    # ``applied`` (composizione assente o non misurata) non aggiunge niente.
    composition = _block(data, "composition")
    proposed = composition.get("proposed")
    proposed = proposed if isinstance(proposed, Mapping) else {}
    applied = composition.get("applied")
    applied = applied if isinstance(applied, Mapping) else {}
    if applied:
        for row in rows:
            if row["id"] == "hook" and applied.get("hook_seconds") is not None:
                if applied.get("hook_applied"):
                    row["composed"] = {
                        "seconds": applied.get("hook_seconds"),
                        "baseline_seconds": applied.get("hook_baseline_seconds"),
                        "asset": applied.get("hook_asset"),
                    }
                    before = applied.get("hook_baseline_seconds")
                    row["why"] = (
                        f"{row['why']}; composizione: {float(applied['hook_seconds']):.2f} s"
                        + (
                            f" (prima {float(before):.2f} s)"
                            if isinstance(before, (int, float))
                            else ""
                        )
                    )
            elif row["id"] == "beat":
                before, after = applied.get("shots_before"), applied.get("shots_after")
                row["composed"] = {"shots_before": before, "shots_after": after}
                if isinstance(before, int) and isinstance(after, int):
                    row["why"] = (
                        f"{row['why']}; composizione: {after} clip in timeline (prima {before})"
                    )
            elif row["id"] == "broll" and proposed.get("broll_order"):
                row["composed"] = {"order": list(proposed["broll_order"])}
                row["why"] = (
                    f"{row['why']}; ordine per nitidezza: "
                    + ", ".join(str(item) for item in proposed["broll_order"])
                )
            elif row["id"] == "cta" and isinstance(applied.get("tail_sec"), (int, float)):
                if float(applied["tail_sec"]) > 0:
                    row["composed"] = {
                        "reserved_tail_sec": applied.get("tail_sec"),
                        "broll_respects_tail": applied.get("broll_respects_tail"),
                    }
                    row["why"] = (
                        f"{row['why']}; composizione: coda di "
                        f"{float(applied['tail_sec']):.2f} s libera da b-roll"
                    )
    return {
        "sections": rows,
        "filled": len(filled),
        "total": len(rows),
        "capabilities": used,
        "unknown_capabilities": unknown,
        "ok": bool(filled) and not unknown,
    }
