"""vx.decide — la mappa di cosa la skill deduce e cosa deve chiedere.

Il motore e' un esecutore: applica una matematica che qualcuno gli ha dato. Questo
modulo e' il punto in cui una richiesta umana diventa un contratto, e soprattutto il
punto in cui la skill si FERMA.

Regola: si deduce solo cio' che ha una risposta unica e verificabile (formato da un
nome di piattaforma, motore da una capacita' richiesta, transizioni dalla presenza di
handle). Si chiede tutto cio' che e' un aggettivo: "vintage" non e' un parametro, e'
un menu.

Un blocco non e' un errore: e' la skill che alza la mano prima di toccare i binari.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["Blocker", "Decision", "interpret", "PLATFORMS", "VAGUE", "ENGINE_TRIGGERS"]

# Piattaforma -> (aspect, larghezza, altezza). Deduzione, non domanda.
PLATFORMS: Dict[str, Dict[str, Any]] = {
    "tiktok": {"aspect": "9:16", "w": 1080, "h": 1920, "engine": "chromium"},
    "reel": {"aspect": "9:16", "w": 1080, "h": 1920, "engine": "chromium"},
    "reels": {"aspect": "9:16", "w": 1080, "h": 1920, "engine": "chromium"},
    "shorts": {"aspect": "9:16", "w": 1080, "h": 1920, "engine": "chromium"},
    "story": {"aspect": "9:16", "w": 1080, "h": 1920, "engine": "chromium"},
    "vlog": {"aspect": "16:9", "w": 1920, "h": 1080, "engine": "pillow"},
    "youtube": {"aspect": "16:9", "w": 1920, "h": 1080, "engine": "pillow"},
    "intervista": {"aspect": "16:9", "w": 1920, "h": 1080, "engine": "pillow"},
    "feed": {"aspect": "1:1", "w": 1080, "h": 1080, "engine": "pillow"},
    "post": {"aspect": "1:1", "w": 1080, "h": 1080, "engine": "pillow"},
    "carosello": {"aspect": "4:5", "w": 1080, "h": 1350, "engine": "pillow"},
}

# Aggettivi che non sono parametri. Ognuno ha un menu numerico: la domanda deve
# poter essere risposta senza sapere cosa c'e' dentro il motore.
VAGUE: Dict[str, Dict[str, Any]] = {
    "vintage": {
        "question": "Il look vintage non e' un parametro. Quale delle due famiglie?",
        "options": [
            "caldo pellicola: shadows #2a1a10, highlights #ffe8c8, grana 4, vignetta PI/9",
            "duotone desaturato: shadows #000000, highlights #ff3333, grana 2",
        ],
    },
    "cinematograf": {
        "question": "Per 'cinematografico' serve una curva, non un aggettivo.",
        "options": [
            "contrasto S: 0/0 0.25/0.22 0.75/0.82 1/1, saturazione invariata",
            "teal-orange: shadows spostate su ciano, highlights su ambra (valori esadecimali)",
        ],
    },
    "dinamic": {
        "question": "'Dinamico' va misurato: a che ritmo taglio?",
        "options": [
            "un taglio per battuta (BPM misurato)",
            "un taglio ogni 2 battute",
            "solo sugli onset",
        ],
    },
    "modern": {
        "question": "Serve una scelta concreta, non un aggettivo.",
        "options": [
            "font geometrico condensato, placca 65%",
            "font grotesque, ombra gaussiana 7px",
        ],
    },
    "elegant": {
        "question": "Definiamo 'elegante' in modo verificabile.",
        "options": [
            "testo nudo con ombra morbida",
            "placca piena al 55%, interlinea 1.2",
        ],
    },
    "epic": {
        "question": "Epico rispetto a cosa?",
        "options": [
            "tagli larghi (2 battute)",
            "accelerazione progressiva sull'ultimo terzo",
        ],
    },
}

# Capacita' che solo il motore browser offre: se le chiedi, l'engine si deduce.
ENGINE_TRIGGERS = {
    "chromium": (
        "css grid",
        "backdrop-filter",
        "font variabil",
        "svg filter",
        "blend mode",
        "mix-blend",
        "webgl",
    ),
    "pillow": ("ombra gaussiana", "sottotitoli", "duotone", "placca"),
}


@dataclass
class Blocker:
    code: str
    question: str
    options: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "question": self.question, "options": self.options}


@dataclass
class Decision:
    ok: bool
    contract: Dict[str, Any] = field(default_factory=dict)
    deduced: Dict[str, Any] = field(default_factory=dict)
    blockers: List[Blocker] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "contract": self.contract,
            "deduced": self.deduced,
            "blockers": [b.to_dict() for b in self.blockers],
        }

    def summary(self) -> str:
        if self.ok:
            return f"contratto chiuso: {self.contract}"
        lines = [f"COMPILAZIONE SOSPESA — {len(self.blockers)} variabili non chiuse:"]
        for i, b in enumerate(self.blockers, 1):
            lines.append(f"  {i}. [{b.code}] {b.question}")
            for opt in b.options:
                lines.append(f"       - {opt}")
        return "\n".join(lines)


def _platform(text: str) -> Optional[str]:
    for name in PLATFORMS:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


def _engine(text: str, default: str) -> str:
    for engine, keys in ENGINE_TRIGGERS.items():
        if any(k in text for k in keys):
            return engine
    return default


def interpret(
    request: str,
    *,
    assets: Optional[Sequence[str]] = None,
    has_handles: bool = True,
) -> Decision:
    """Traduce una richiesta in contratto, oppure si ferma con le domande.

    `assets` sono i nomi di font/loghi disponibili nel registro: se la richiesta ne
    nomina uno che non c'e', non si inventa un fallback silenzioso.
    """
    text = (request or "").lower()
    decision = Decision(ok=True)

    platform = _platform(text)
    if platform:
        p = PLATFORMS[platform]
        decision.deduced.update(
            {
                "aspect": p["aspect"],
                "width": p["w"],
                "height": p["h"],
                "platform": platform,
                "reason": f"piattaforma '{platform}' implica {p['aspect']}",
            }
        )
        decision.contract.update(
            {"canvas": {"width": p["w"], "height": p["h"], "aspect_ratio": p["aspect"]}}
        )
    else:
        decision.blockers.append(
            Blocker(
                "format_missing",
                "Il formato di uscita non e' deducibile dalla richiesta.",
                [
                    "9:16 1080x1920 (TikTok/Reel)",
                    "16:9 1920x1080 (vlog)",
                    "1:1 1080x1080 (feed)",
                    "4:5 1080x1350 (carosello)",
                ],
            )
        )

    for word, spec in VAGUE.items():
        if re.search(rf"\b{re.escape(word)}", text):
            decision.blockers.append(
                Blocker(f"vague_{word}", spec["question"], list(spec["options"]))
            )

    if "transizion" in text:
        if has_handles:
            decision.deduced["transitions"] = {
                "model": "overlap",
                "duration_s": 0.25,
                "reason": "handle disponibili: la timeline non si accorcia",
            }
        else:
            decision.blockers.append(
                Blocker(
                    "no_handles",
                    "Le clip non hanno coda sufficiente per la sovrapposizione: "
                    "la timeline si accorcerebbe o la transizione va ridotta.",
                    [
                        "accetta l'accorciamento dichiarato",
                        "transizione piu' corta",
                        "nessuna transizione",
                    ],
                )
            )

    if assets is not None:
        named = re.findall(r"\bfont\s+([A-Za-z0-9_-]+)", request or "", re.I)
        known = {a.lower() for a in assets}
        for name in named:
            if name.strip().lower() not in known:
                decision.blockers.append(
                    Blocker(
                        "asset_missing",
                        f"Il font '{name.strip()}' non e' nel registro: "
                        "forniscilo o autorizza un fallback tracciabile.",
                        [
                            f"usa {sorted(known)[0] if known else 'Antonio'} (sostituzione tracciata)",
                            "carico il file del font",
                        ],
                    )
                )

    engine = _engine(text, decision.deduced.get("engine", "pillow"))
    decision.deduced["engine"] = engine
    decision.contract["engine"] = engine
    decision.contract.setdefault("grade", {"type": "none"})
    decision.contract["closed"] = not decision.blockers
    decision.ok = not decision.blockers
    return decision
