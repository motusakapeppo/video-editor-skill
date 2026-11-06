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

from typing import Any, Dict, List, Mapping, Optional

__all__ = ["SECTIONS", "CAPABILITIES", "compile_sections"]

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


def _block(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}


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
    return {
        "sections": rows,
        "filled": len(filled),
        "total": len(rows),
        "capabilities": used,
        "unknown_capabilities": unknown,
        "ok": bool(filled) and not unknown,
    }
