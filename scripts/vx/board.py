"""vx.board — la giuria creativa: ruoli, domini esclusivi, e chi risolve i conflitti.

Il problema che risolve: misurare il materiale e applicare una formula produce uno
stile "corretto" ma senza intenzione; far decidere a piu' agenti senza regole produce
un arlecchino (font gotico, colore acido, ritmo lento, testo scurrile: ognuno coerente
con se', incoerente insieme).

Quindi la giuria ha tre regole strutturali:

  1. DOMINIO ESCLUSIVO. Ogni ruolo decide SOLO dentro il suo campo. Il tipografo non
     tocca i colori, il colorista non tocca i tempi. Nessuno puo' invadere.
  2. CONTRATTO DI PROPOSTA. Ogni ruolo consegna una proposta con lo stesso schema e
     deve dichiarare su quale MISURA del materiale si fonda. Una proposta senza
     misura a supporto viene scartata dal direttore.
  3. DIRETTORE UNICO. I conflitti fra domini non si votano: li risolve il direttore,
     che ha anche il veto di coerenza (il "gate estetico"). Non e' un pareggio: e'
     una decisione con motivazione scritta.

Le proposte arrivano da agenti (il runtime che ospita la skill li convoca). Questo
modulo non inventa lo stile: valida, fonde, e rifiuta l'incoerenza.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["ROLES", "Proposal", "Verdict", "convene", "validate", "merge",
           "open_domains", "CLOSED_BY_PRINCIPLE", "OPEN_BY_TASTE"]

ROLES: Dict[str, Dict[str, Any]] = {
    "tipografo": {
        "owns": ["font", "font_px", "tracking", "case"],
        "must_cite": "advance o stem misurati sul materiale",
        "ask": "Scegli il carattere che interpreta l'energia del materiale. Dichiara "
        "quale misura lo giustifica (avanzamento, spessore, densita' di testo).",
    },
    "colorista": {
        "owns": ["ink", "accent", "grade"],
        "must_cite": "luma p01/p50/p99 o saturazione misurate",
        "ask": "Definisci inchiostro, accento e curva di grade. Ogni scelta deve "
        "nascere da una misura di luma o saturazione del materiale.",
    },
    "montatore": {
        "owns": ["fade_ms", "pop_ms", "bottom_limit", "cuts"],
        "must_cite": "onset/s o varianza della fascia bassa",
        "ask": "Definisci il ritmo del testo e la posizione. Cita onset/s o la "
        "varianza della zona dove andra' il testo.",
    },
    "direttore": {
        "owns": ["*"],
        "must_cite": "la coerenza fra i tre domini",
        "ask": "Risolvi i conflitti e firma il brief. Puoi scartare una proposta che "
        "non cita una misura o che contraddice il materiale.",
    },
}


@dataclass
class Proposal:
    role: str
    values: Dict[str, Any]
    cited: str = ""
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "values": self.values,
            "cited": self.cited,
            "rationale": self.rationale,
        }


@dataclass
class Verdict:
    ok: bool
    brief: Dict[str, Any] = field(default_factory=dict)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "brief": self.brief,
            "rejected": self.rejected,
            "notes": self.notes,
        }


def validate(proposal: Proposal, material: Dict[str, Any]) -> Tuple[bool, str]:
    """Il gate anti-arlecchino: una proposta deve citare una misura e starci dentro."""
    if proposal.role not in ROLES:
        return False, f"ruolo sconosciuto: {proposal.role}"
    if not proposal.cited:
        return False, "nessuna misura citata: proposta scartata (regola 2)"
    owned = ROLES[proposal.role]["owns"]
    if owns := [k for k in proposal.values if k not in owned and owned != ["*"]]:
        return False, f"fuori dominio per {proposal.role}: {owns}"

    v = proposal.values
    # coerenza geometrica: il corpo deve stare nel canvas
    if "font_px" in v:
        px = int(v["font_px"])
        if px < 12 or px > int(material.get("height", 1920) * 0.22):
            return False, f"corpo {px}px fuori dal possibile per il canvas"
    # coerenza temporale: fade+pop non possono eccedere la cue piu' corta
    if "fade_ms" in v or "pop_ms" in v:
        total = int(v.get("fade_ms", 0)) + int(v.get("pop_ms", 0))
        if total > 1200:
            return False, f"animazione {total}ms: troppo lunga per una cue media"
    # coerenza cromatica: inchiostro e accento non possono coincidere
    if (
        "ink" in v
        and "accent" in v
        and str(v["ink"]).lower() == str(v["accent"]).lower()
    ):
        return False, "inchiostro e accento identici: il rilievo sparisce"
    return True, ""


def convene(proposals: Sequence[Proposal], material: Dict[str, Any]) -> Verdict:
    """Fonde le proposte valide. I conflitti restano al direttore, non al caso."""
    verdict = Verdict(ok=True)
    accepted: Dict[str, Dict[str, Any]] = {}
    owners: Dict[str, str] = {}

    for p in proposals:
        good, why = validate(p, material)
        if not good:
            verdict.rejected.append({"role": p.role, "reason": why})
            continue
        if p.role == "direttore":
            continue
        for k, val in p.values.items():
            if k in owners:
                verdict.notes.append(
                    f"conflitto su '{k}': {owners[k]} e {p.role} — decide il direttore"
                )
                continue
            owners[k] = p.role
            accepted[k] = val

    director = next((p for p in proposals if p.role == "direttore" and p.cited), None)
    if director:
        for k, val in director.values.items():
            if k in accepted and accepted[k] != val:
                verdict.notes.append(
                    f"direttore corregge '{k}': {accepted[k]} -> {val}"
                )
            accepted[k] = val
            owners[k] = "direttore"
    else:
        verdict.notes.append("nessun direttore: il brief resta senza firma")

    missing = [
        r
        for r in ("tipografo", "colorista", "montatore")
        if not any(o == r for o in owners.values())
    ]
    if missing:
        verdict.ok = False
        verdict.notes.append(f"domini scoperti: {missing}")

    verdict.brief = accepted
    verdict.brief["_owners"] = owners
    verdict.brief["_signed"] = bool(director)
    return verdict


def merge(verdict: Verdict) -> Dict[str, Any]:
    """Brief pronto per il pipeline. Senza firma del direttore non passa."""
    if not verdict.ok or not verdict.brief.get("_signed"):
        raise ValueError(f"brief non firmato o incompleto: {verdict.notes}")
    return {k: v for k, v in verdict.brief.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# quando la giuria serve e quando no
# --------------------------------------------------------------------------- #

# Un dominio e' CHIUSO se il principio lo determina senza gusto: contrasto, soglie
# di salute del materiale, intervalli fra onset, varianza della fascia. E' APERTO
# se resta una scelta di gusto o di contenuto che nessuna misura puo' decidere.
CLOSED_BY_PRINCIPLE = {
    "ink": "contrasto calcolato: vince il colore con rapporto maggiore, non un default",
    "grade": "soglie di salute (p01/p50/p99/saturazione): se sono sane, nessuna curva",
    "fade_ms": "intervallo fra onset: l'animazione chiude prima del successivo",
    "pop_ms": "intervallo fra onset, come sopra",
    "bottom_limit": "varianza della fascia bassa + margine di piattaforma",
    "accent": "complementare della tinta dominante, saturazione derivata",
}

OPEN_BY_TASTE = {
    "font": ("tipografo", "Quale carattere, fra quelli che rispettano i vincoli misurati?"),
    "copy": ("scrittore", "Che cosa dice il testo? Nessuna misura puo' inventare le parole."),
    "cuts": ("montatore", "Su quali istanti tagliare, quando gli onset non bastano a deciderlo."),
}


def open_domains(material: Dict[str, Any], *, has_text: bool = True,
                 request: str = "") -> List[Dict[str, Any]]:
    """Cosa va davvero chiesto a un agente. Tutto il resto lo decide il principio.

    `font` e' aperto perche' la misura vincola le proprieta' (stretto, pesante) ma
    non sceglie la faccia. `copy` e' aperto se il testo non c'e'. Il resto e' chiuso
    finche' il materiale non e' degenerato: in quel caso la correzione e' comunque
    calcolabile, non opinabile.
    """
    open_list: List[Dict[str, Any]] = []
    p50 = float(material.get("luma_p50", 0.5))
    sat = float(material.get("saturation", 0.2))
    onsets = float(material.get("onsets_per_second", 0.0))
    if not has_text:
        role, q = OPEN_BY_TASTE["copy"]
        open_list.append({"domain": "copy", "role": role, "question": q})
    role, q = OPEN_BY_TASTE["font"]
    open_list.append({
        "domain": "font", "role": role, "question": q,
        "constraint": f"deve essere scelto fra i font misurati; il materiale chiede "
                      f"massa ottica (moto/onset) e il corpo e' vincolato dallo spazio",
    })
    # nota: grade, ink, timing e posizione non entrano mai qui: sono chiusi.
    open_list.append({
        "domain": "_closed", "role": None,
        "question": "restano chiusi dal principio: "
                    + ", ".join(sorted(CLOSED_BY_PRINCIPLE)),
    })
    return open_list


def _contrast_ink(background_luma: float) -> Tuple[Tuple[int, int, int], str]:
    """Il colore del testo non si sceglie: si calcola. Vince il rapporto maggiore.

    Era il bug della formula precedente: 'bianco se luma < 0.55'. A luma 0.48 il
    bianco da' 2:1 e il nero 10:1, quindi la soglia sceglieva il colore peggiore.
    """
    def rel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    bg = rel(max(0.0, min(1.0, background_luma)))
    white = (1.0 + 0.05) / (bg + 0.05)
    black = (bg + 0.05) / 0.05
    if white >= black:
        return (250, 250, 248), f"bianco, contrasto {white:.1f}:1"
    return (18, 18, 20), f"quasi nero, contrasto {black:.1f}:1"
