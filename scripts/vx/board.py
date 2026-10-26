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
           "open_domains", "CLOSED_BY_PRINCIPLE", "OPEN_BY_TASTE",
           "context_for", "verify_numbers", "METHODS",
           "ANIMATION_CAPABILITIES", "required_engine", "verify_animation"]

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
    "animatore": {
        "owns": ["animation"],
        "must_cite": "intervallo fra onset, durata delle cue, energia del materiale",
        "ask": "Decidi COME si muove ogni cosa: testo, elementi grafici, transizioni fra "
               "tagli. La decisione e' PER CUE e PER ELEMENTO: possono essere tutte "
               "uguali, diverse, o assenti. Ogni scelta deve citare un intervallo o una "
               "durata misurata.",
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
    # Un agente che ha un dubbio NON indovina: restituisce la domanda. Il brief non
    # si chiude e la domanda arriva all'utente tramite la chat principale.
    questions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "values": self.values,
                "cited": self.cited, "rationale": self.rationale,
                "questions": self.questions}

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

    # un agente che chiede non e' un agente che ha deciso: il brief resta aperto
    _pending: List[Dict[str, Any]] = []
    for _p in proposals:
        for _q in getattr(_p, "questions", []) or []:
            _pending.append({"role": _p.role, **_q})
    if _pending:
        verdict.ok = False
        verdict.notes.append(f"{len(_pending)} domande aperte: il brief non si chiude")
    pending: List[Dict[str, Any]] = []
    for p in proposals:
        for q in p.questions:
            pending.append({"role": p.role, **q})
    if pending:
        verdict.ok = False
        verdict.notes.append(f"{len(pending)} domande aperte: il brief non si chiude")
        verdict.brief["_questions"] = pending

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

    covered = set(owners.values())
    if "animatore" in covered:
        covered.add("montatore")   # l'animatore possiede anche i tempi
    missing = [r for r in ("tipografo", "colorista", "montatore") if r not in covered]
    if missing:
        verdict.ok = False
        verdict.notes.append(f"domini scoperti: {missing}")

    verdict.brief = accepted
    if _pending:
        verdict.brief["_questions"] = _pending
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


# --------------------------------------------------------------------------- #
# il contesto porta il metodo; l'agente ragiona e mostra i passaggi
# --------------------------------------------------------------------------- #

METHODS: Dict[str, str] = {
    "animatore": (
        "METODO DELL'ANIMAZIONE.\n"
        "DATI DA USARE: intervallo_onset = 1/onsets_per_second; durata di ogni cue; moto.\n"
        "SCHEMA OBBLIGATORIO (una voce per cue, piu' una per elemento):\n"
        "  animation = {\n"
        "    \"per_cue\": [{\"cue\": 0, \"in\": \"pop|fade|slide_up|slide_left|typeon|none\",\n"
        "                  \"ms\": <int>, \"easing\": \"ease_out_back|ease_out|linear\"},\n"
        "                 ...],\n"
        "    \"elements\": [{\"name\": \"label\", \"effect\": \"none|pulse|fade\",\n"
        "                    \"amp\": <0..0.08>, \"ms\": <int>}],\n"
        "    \"transitions\": {\"style\": \"hard|fade\", \"ms\": <int>}\n"
        "  }\n"
        "REGOLE: (a) ms + ms non puo' superare l'intervallo fra onset, se le animazioni\n"
        "  insistono sulla stessa parola; (b) lo scale non deve MAI superare 1.0, altrimenti\n"
        "  il Gate 4 perde validita'; (c) PUOI variare per cue: alternare pop e slide_up su\n"
        "  cue consecutive e' legittimo; (d) ANTI-ARLECCHINO: al massimo 2 trattamenti\n"
        "  distinti per video, salvo richiesta esplicita; (e) se il materiale e' lento e\n"
        "  uniforme, 'none' e' una risposta corretta e va dichiarata come tale.\n"
        "  (f) se la richiesta nomina un'animazione, deve comparire nell'output."
    ),
    "colorista": (
        "METODO DEL CONTRASTO (WCAG), da applicare alla lettera:\n"
        "1. La luma misurata del materiale e' in spazio sRGB, non lineare.\n"
        "2. Linearizza:  L = c/12.92  se c <= 0.03928,  altrimenti ((c+0.055)/1.055)^2.4\n"
        "3. Rapporto col bianco: (1.0+0.05)/(L+0.05). Col nero: (L+0.05)/0.05\n"
        "4. Si sceglie il colore col rapporto MAGGIORE, non per soglia.\n"
        "Esempio: c=0.4845 -> L=0.200 -> bianco 4.2:1, nero 5.0:1 -> vince il nero.\n"
        "5. Riporta nel reasoning i due rapporti calcolati, con i numeri."
    ),
    "tipografo": (
        "METODO DEL CORPO:\n"
        "1. px = (larghezza_canvas * frazione_occupata) / (advance * n_caratteri)\n"
        "2. frazione_occupata va dichiarata e giustificata con l'energia del materiale\n"
        "   (piu' onsets/s -> testo piu' grande e piu' pesante, meno tempo per leggerlo).\n"
        "3. Verifica che l'altezza del blocco (righe * px * 1.45) stia sopra il limite.\n"
        "4. Riporta il calcolo nel reasoning."
    ),
    "montatore": (
        "METODO DEI TEMPI:\n"
        "1. intervallo_onset = 1 / onsets_per_second (secondi).\n"
        "2. fade_ms + pop_ms deve stare sotto intervallo_onset*1000, se le animazioni\n"
        "   sono sequenziali sulla stessa parola; altrimenti sotto la cue media.\n"
        "3. bottom_limit: altezza - margine_piattaforma - altezza_blocco.\n"
        "4. Riporta i millisecondi calcolati nel reasoning."
    ),
}


def context_for(role: str, material: Dict[str, Any], *, extra: str = "") -> str:
    """Il contesto che si passa all'agente: dati, metodo, vincoli, e cosa deve mostrare.

    Il campo `reasoning` non e' decorativo: e' la parte che il verificatore rilegge.
    """
    method = METHODS.get(role, "")
    return (
        f"RUOLO: {role}\n"
        f"MATERIALE (misurato, non stimato): {material}\n"
        f"VINCOLI: il risultato deve stare nel canvas e nei tempi della cue.\n"
        f"{method}\n"
        f"OUTPUT: JSON con `values`, `cited`, e `reasoning` dove mostri i passaggi "
        f"numerici (non un'aggettivazione).\n"
        f"{extra}"
    )


def verify_numbers(proposal: "Proposal", material: Dict[str, Any]) -> List[str]:
    """Ricalcola cio' che l'agente dichiara. Non sostituisce il ragionamento: lo controlla.

    Se un numero non torna, l'errore viene rimandato all'agente per la correzione.
    """
    errs: List[str] = []
    v = proposal.values
    if "ink" in v and "luma_p50" in material:
        c = float(material["luma_p50"])
        lin = c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        white = (1.05) / (lin + 0.05)
        black = (lin + 0.05) / 0.05
        try:
            h = str(v["ink"]).lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            l_ink = 0.2126 * (r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4) \
                + 0.7152 * (g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4) \
                + 0.0722 * (b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4)
            ratio = (max(lin, l_ink) + 0.05) / (min(lin, l_ink) + 0.05)
        except Exception:  # noqa: BLE001
            errs.append(f"ink non interpretabile: {v['ink']}")
            return errs
        best = max(white, black)
        if ratio < best - 0.15:
            errs.append(
                f"ink {v['ink']} da {ratio:.2f}:1 ma il colore migliore da {best:.2f}:1 "
                f"(bianco {white:.2f}, nero {black:.2f}) — ricalcola con la linearizzazione"
            )
    if "font_px" in v and "font" in v:
        px = int(v["font_px"])
        if px < 12:
            errs.append(f"corpo {px}px illeggibile")
    return errs


# --------------------------------------------------------------------------- #
# vocabolario delle animazioni e motore che le sa eseguire
# --------------------------------------------------------------------------- #

# Ogni effetto dichiara il motore minimo che lo sa fare. Non e' una preferenza:
# e' una capacita'. Se l'animatore chiede un effetto che Pillow non sa eseguire,
# il motore sale a Chromium automaticamente.
ANIMATION_CAPABILITIES: Dict[str, str] = {
    "none": "pillow",
    "fade": "pillow",
    "pop": "pillow",
    "slide_up": "pillow",
    "slide_left": "pillow",
    "slide_right": "pillow",
    "typeon": "pillow",
    "blur_in": "chromium",       # sfocatura animata del testo o dello sfondo
    "glitch": "chromium",        # displacement / RGB split
    "mask_wipe": "chromium",     # clip-path / gradient mask
    "blend_difference": "chromium",  # mix-blend-mode contro il video vivo
    "elastic_3d": "chromium",    # prospettiva / rotate3d con easing elastico
}

_ENGINE_RANK = {"pillow": 0, "chromium": 1}


def required_engine(animation: Optional[Dict[str, Any]]) -> str:
    """Il motore minimo che sa eseguire TUTTO cio' che l'animazione chiede."""
    if not animation:
        return "pillow"
    need = "pillow"
    effects: List[str] = []
    for cue in animation.get("per_cue", []) or []:
        effects.append(str(cue.get("in", "none")))
    for el in animation.get("elements", []) or []:
        effects.append(str(el.get("effect", "none")))
    tr = animation.get("transitions") or {}
    if tr.get("style") and tr.get("style") != "hard":
        effects.append("fade")
    for eff in effects:
        engine = ANIMATION_CAPABILITIES.get(eff)
        if engine is None:
            raise ValueError(f"effetto non nel vocabolario: {eff!r}")
        if _ENGINE_RANK[engine] > _ENGINE_RANK[need]:
            need = engine
    return need


def verify_animation(animation: Dict[str, Any], cues: Sequence[Dict[str, float]],
                     *, onset_interval_ms: float = 348.0) -> List[str]:
    """Le tre regole non negoziabili, ricalcolate."""
    errs: List[str] = []
    if not animation:
        return errs
    for item in animation.get("per_cue", []) or []:
        ms = float(item.get("ms", 0))
        idx = int(item.get("cue", 0))
        if ms > onset_interval_ms:
            errs.append(f"cue {idx}: {ms:.0f}ms > intervallo fra onset {onset_interval_ms:.0f}ms")
        if idx < len(cues):
            available = (cues[idx]["end"] - cues[idx]["start"]) * 1000.0
            if ms > available:
                errs.append(f"cue {idx}: {ms:.0f}ms > durata cue {available:.0f}ms")
        if str(item.get("in")) not in ANIMATION_CAPABILITIES:
            errs.append(f"cue {idx}: effetto sconosciuto {item.get('in')!r}")
    for el in animation.get("elements", []) or []:
        amp = float(el.get("amp", 0))
        if amp < 0 or amp > 0.08:
            errs.append(f"elemento {el.get('name')}: ampiezza {amp} fuori da [0, 0.08]")
    treatments = {str(c.get("in")) for c in animation.get("per_cue", []) or []}
    if len(treatments) > 2:
        errs.append(f"anti-arlecchino: {len(treatments)} trattamenti distinti (max 2)")
    return errs


# --------------------------------------------------------------------------- #
# prefight: cosa chiedere all'utente prima di convocare gli agenti
# --------------------------------------------------------------------------- #

# Si chiede solo cio' che CAMBIA l'output e non e' deducibile ne' misurabile.
PREFLIGHT_RULES: List[Dict[str, Any]] = [
    {"code": "tone", "triggers": ("caotic", "festos", "elegant", "minimal", "vintage",
                                  "cinematograf", "epic", "modern"),
     "question": "Il tono e' un aggettivo: quale intensita' vuoi, in concreto?",
     "options": ["contenuta (1 trattamento, palette 2 colori)",
                 "media (2 trattamenti, palette 3 colori)",
                 "massima (4 trattamenti, palette 4 colori, jitter di rotazione)"]},
    {"code": "copy", "triggers": ("spot", "promo", "pubblicit"),
     "question": "Chi parla e cosa deve restare impresso? Serve il nome e la data/CTA.",
     "options": ["te li fornisco io", "inventali tu dal materiale"]},
    {"code": "palette", "triggers": ("colorat", "festos"),
     "question": "Il colore deve venire dal materiale o essere imposto?",
     "options": ["deriva dal materiale (complementare della tinta dominante)",
                 "palette fissa che ti do io"]},
]


def prefight(request: str, material: Dict[str, Any], *, has_text: bool = True,
             assets: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Domande da porre all'utente PRIMA di convocare la giuria.

    Non chiede cio' che si misura (luma, contrasto, durate) ne' cio' che la
    piattaforma decide (formato). Chiede solo le scelte che cambiano l'output e che
    nessun dato puo' risolvere: tono, contenuto, palette imposta.
    """
    text = (request or "").lower()
    questions: List[Dict[str, Any]] = []
    import re as _re
    has_quoted = bool(_re.search(r"[\"«'][^\"»']{6,}[\"»']", request or ""))
    has_date = bool(_re.search(r"\b\d{1,2}\s*(novembre|dicembre|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre)\b", text))
    for rule in PREFLIGHT_RULES:
        if rule["code"] == "copy" and (has_quoted or has_date):
            continue
        if any(trig in text for trig in rule["triggers"]):
            questions.append({"code": rule["code"], "question": rule["question"],
                              "options": list(rule["options"])})
    if not has_text:
        questions.append({"code": "text_missing",
                          "question": "Non c'e' testo: chi lo scrive?",
                          "options": ["lo scrivo io", "lo inventa lo scrittore"]})
    if assets is not None and not assets:
        questions.append({"code": "no_assets",
                          "question": "Nessun font/logo nel registro: procedo col sistema?",
                          "options": ["usa i font di sistema", "ti fornisco gli asset"]})
    return {"ok": not questions, "questions": questions}
