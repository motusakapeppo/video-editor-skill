"""vx.decide — la mappa di cosa la skill deduce e cosa deve chiedere.

Il motore e' un esecutore: applica una matematica che qualcuno gli ha dato. Questo
modulo e' il punto in cui una richiesta umana diventa un contratto, e soprattutto il
punto in cui la skill si FERMA.

Regola: si deduce solo cio' che ha una risposta unica e verificabile (formato da un
nome di piattaforma, motore da una capacita' richiesta, transizioni dalla presenza di
handle). Si chiede tutto cio' che e' un aggettivo: "vintage" non e' un parametro, e'
un menu.

Un blocco non e' un errore: e' la skill che alza la mano prima di toccare i binari.
In regime autonomo, invece, la skill sceglie e motiva: `parse_and_decide` compila
il payload creativo e `creative_rationale` dice perche'. I gate fisici restano a
valle e non vengono indeboliti.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import contract

__all__ = [
    "AmbiguousContractError",
    "Blocker",
    "Decision",
    "ENGINE_TRIGGERS",
    "PLATFORMS",
    "VAGUE",
    "calculate_specificity_score",
    "duotone_filter",
    "interpret",
    "parse_and_decide",
    "payload_to_edit_plan",
    "select_mode",
    "validate_scene_payload",
]

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


# --------------------------------------------------------------------------- #
# dual regime: STRICT (execute) / AUTONOMOUS (choose and justify)
# --------------------------------------------------------------------------- #

SPECIFICITY_TOTAL = 7
STRICT_THRESHOLD = 0.7

GEOMETRY_RE = re.compile(
    r"\b(9:16|16:9|1:1|4:5|1080x1920|1920x1080|1080x1080)\b", re.IGNORECASE
)
BPM_RE = re.compile(r"\b(\d+)\s*bpm\b", re.IGNORECASE)
HEX_RE = re.compile(r"#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})\b")
FONT_RE = re.compile(r"\bfont\s+([\w-]+)\b", re.IGNORECASE)
CUT_RE = re.compile(
    r"\b(un taglio per|taglio ogni|su ogni battuta|sugli onset)\b", re.IGNORECASE
)
DURATION_RE = re.compile(r"\b(\d+)\s*(s|sec|secondi)\b", re.IGNORECASE)
ENGINE_RE = re.compile(r"\bengine\s*=\s*(pillow|chromium)\b", re.IGNORECASE)


class AmbiguousContractError(ValueError):
    """STRICT mode refused a vague adjective that has no executable value."""


def calculate_specificity_score(prompt: str) -> tuple[float, dict]:
    text = prompt or ""
    matches: Dict[str, bool] = {}
    if GEOMETRY_RE.search(text):
        matches["geometry"] = True
    if BPM_RE.search(text):
        matches["bpm"] = True
    if HEX_RE.search(text):
        matches["color"] = True
    if FONT_RE.search(text):
        matches["typography"] = True
    if CUT_RE.search(text):
        matches["cut_policy"] = True
    if DURATION_RE.search(text):
        matches["duration"] = True
    if ENGINE_RE.search(text):
        matches["engine"] = True
    return round(len(matches) / SPECIFICITY_TOTAL, 2), matches


def select_mode(prompt: str) -> str:
    score, _ = calculate_specificity_score(prompt)
    return "strict" if score >= STRICT_THRESHOLD else "autonomous"


def _font_choice(
    text: str, assets: Optional[Sequence[str]]
) -> tuple[str, Optional[str]]:
    named = FONT_RE.findall(text)
    font = named[0] if named else "Antonio"
    if assets is None:
        return font, None
    known = {a.lower() for a in assets}
    if font.lower() in known:
        return font, None
    return "Antonio", f"font {font} non disponibile: uso Antonio (tracciato)"


def parse_and_decide(
    prompt: str,
    assets: Optional[Sequence[str]] = None,
    *,
    asset_pool: Optional[Sequence[Any]] = None,
    force_mode: Optional[str] = None,
    montage: bool = False,
) -> Dict[str, Any]:
    """Classify the request, then either execute (strict) or choose (autonomous).

    `assets` remains the font registry: names only. `asset_pool` is a sequence of
    `asset_triage.AssetInfo`; when non-empty the payload carries the triage
    verdict under `asset_triage`. `force_mode` overrides only the strict/autonomous
    decision, never the specificity score.
    """
    if force_mode not in (None, "strict", "autonomous"):
        raise contract.VxError(
            f"force_mode {force_mode!r} not in None/strict/autonomous"
        )
    text = prompt or ""
    lower = text.lower()
    score, matches = calculate_specificity_score(text)
    detected_mode = "strict" if score >= STRICT_THRESHOLD else "autonomous"
    mode = force_mode or detected_mode
    vintage = bool(re.search(r"\b(vintage|retr[oò])\b", lower))
    dynamic = bool(re.search(r"\b(dinamic|incalzante)", lower))

    if mode == "strict":
        if vintage and "color" not in matches:
            raise AmbiguousContractError(
                "[vague_vintage] il look vintage non e' un parametro quantificato: "
                "specifica una palette hex (es. #1A100C / #FFE8C8)."
            )
        if dynamic and "cut_policy" not in matches and "bpm" not in matches:
            raise AmbiguousContractError(
                "[vague_dynamic] il ritmo dinamico va misurato: specifica BPM o "
                "una regola di taglio su battuta/onset."
            )

    platform_name = _platform(lower)
    rd_search = mode == "autonomous" and platform_name is None
    platform = platform_name or "tiktok"
    spec = PLATFORMS[platform]

    font, font_note = _font_choice(text, assets)
    if mode == "strict" and font_note:
        raise AmbiguousContractError(
            f"[asset_missing] {font_note}. Fornisci il font o autorizza la "
            "sostituzione in autonomia."
        )

    hexes = [f"#{value}" for value in HEX_RE.findall(text)]
    bpm_match = BPM_RE.search(text)
    bpm = int(bpm_match.group(1)) if bpm_match else None
    engine_match = ENGINE_RE.search(text)
    engine = engine_match.group(1).lower() if engine_match else _engine(lower, "pillow")

    if mode == "strict":
        color = (
            {
                "type": "custom",
                "colors": hexes,
                "rationale": "colori espliciti nel prompt",
            }
            if hexes
            else {
                "type": "none",
                "rationale": "nessun colore nel prompt: resta neutro",
            }
        )
        cut = (
            "onset_synced"
            if "onset" in lower
            else ("beat_synced" if bpm or "battuta" in lower else "standard")
        )
        pacing = {
            "bpm_target": bpm,
            "cut_policy": cut,
            "rationale": (
                "ritmo dichiarato nel prompt"
                if bpm or cut != "standard"
                else "nessun ritmo nel prompt: taglio standard"
            ),
        }
        typography = {
            "font_family": font,
            "size_px": 72,
            "rationale": (
                "font dichiarato nel prompt"
                if FONT_RE.search(text)
                else "font di default del repo"
            ),
        }
    else:
        color = {
            "type": "duotone" if vintage else "none",
            "shadows": "#1A100C" if vintage else "#000000",
            "highlights": "#FFE8C8" if vintage else "#FFFFFF",
            "rationale": (
                "palette calda pellicola dedotta dal contesto"
                if vintage
                else "nessuna alterazione cromatica"
            ),
        }
        pacing = {
            "bpm_target": 120 if dynamic else 100,
            "cut_policy": "beat_synced" if dynamic else "standard",
            "rationale": (
                "ritmo dinamico sincronizzato a 120 BPM"
                if dynamic
                else "ritmo narrativo standard"
            ),
        }
        typography = {
            "font_family": font,
            "size_px": 72,
            "rationale": font_note or "font certificato per la safe area",
        }

    payload: Dict[str, Any] = {
        "contract_version": contract.VERSION,
        "execution_context": {
            "mode": mode,
            "specificity_score": score,
            "creative_rationale": {
                "genre_detected": f"{platform}_promo",
                "pacing": pacing,
                "color_palette": color,
                "typography": typography,
                "rd_search_required": rd_search,
                "rd_search_conducted": False,
                "rd_search_sources": [],
            },
        },
        "scene": {
            "aspect_ratio": spec["aspect"],
            "resolution": [spec["w"], spec["h"]],
            "engine": engine,
        },
    }
    if asset_pool is not None and len(asset_pool) > 0:
        # Local import: asset_triage imports contract and probe, so a module-level
        # import here would risk a cycle.
        from . import asset_triage

        triage = asset_triage.triage_assets(
            asset_pool, (spec["w"], spec["h"]), montage=montage
        )
        payload["asset_triage"] = triage.payload_block()
    return payload


def _hex_rgb(value: object) -> tuple[float, float, float]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return (0.0, 0.0, 0.0)
    try:
        return tuple(int(text[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.0)


def duotone_filter(shadows: object, highlights: object) -> str:
    sr, sg, sb = _hex_rgb(shadows)
    hr, hg, hb = _hex_rgb(highlights)
    return (
        "hue=s=0,"
        f"curves=r='0/{sr:.3f} 1/{hr:.3f}':"
        f"g='0/{sg:.3f} 1/{hg:.3f}':"
        f"b='0/{sb:.3f} 1/{hb:.3f}'"
    )


def payload_to_edit_plan(
    payload: Dict[str, Any], media_info: Dict[str, Any]
) -> contract.EditPlan:
    errors = validate_scene_payload(payload)
    if errors:
        raise contract.VxError("invalid scene payload: " + "; ".join(errors))
    try:
        duration = float(media_info.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise contract.VxError("media_info.duration is required")

    scene = payload["scene"]
    rationale = payload["execution_context"]["creative_rationale"]
    aspect = str(scene["aspect_ratio"])
    width, height = (int(value) for value in scene["resolution"])
    finding = {
        "id": "brief.autonomous",
        "severity": "info",
        "metric": "brief.mode",
        "value": "autonomous",
        "threshold": "n/a",
        "evidence": rationale.get("genre_detected") or "creative_rationale",
        "suggested_treatment": None,
        "span": [0.0, duration],
        "declared": True,
    }

    treatments: List[Dict[str, Any]] = []
    executed = ["output", "cut"]
    color = rationale.get("color_palette") or {}
    if color.get("type") == "duotone":
        filter_text = duotone_filter(color.get("shadows"), color.get("highlights"))
        witness = {"finding_id": "brief.autonomous", "t0": 0.0, "t1": duration}
        source_id = media_info.get("source_id")
        if isinstance(source_id, str) and source_id:
            witness["source_id"] = source_id
        treatments.append(
            {
                "name": "color_fix",
                "kind": "technical",
                "enabled": True,
                "params": {"filter": filter_text},
                "reason": {
                    "finding_id": "brief.autonomous",
                    "explain": str(color.get("rationale") or "duotone autonomo"),
                },
                "evidence": [witness],
            }
        )
        executed.append("color_fix")

    pacing = rationale.get("pacing") or {}
    typography = rationale.get("typography") or {}
    engine = str(scene.get("engine") or "pillow")
    unsupported: List[Dict[str, str]] = []
    if pacing.get("bpm_target") is not None:
        unsupported.append(
            {
                "field": "pacing.bpm_target",
                "reason": "il pianificatore non taglia su BPM: il valore resta nel brief",
            }
        )
    if engine != "pillow":
        unsupported.append(
            {
                "field": "scene.engine",
                "reason": "vx run usa ffmpeg; il compositor non è cablato",
            }
        )
    if typography.get("font_family"):
        unsupported.append(
            {
                "field": "typography.font_family",
                "reason": "nota di stile: il bridge non crea sottotitoli",
            }
        )
    if "asset_triage" in payload:
        unsupported.append(
            {
                "field": "asset_triage",
                "reason": (
                    "il bridge EditPlan non esegue una timeline multitrack: "
                    "il verdetto di triage resta nel payload"
                ),
            }
        )

    return contract.EditPlan(
        output={"aspect": aspect, "width": width, "height": height},
        treatments=treatments,
        cuts=[{"in": 0.0, "out": duration}],
        render={"precision": "full", "engine": engine},
        brief={
            "creative_rationale": rationale,
            "font": typography.get("font_family"),
            "bpm": pacing.get("bpm_target"),
            "autonomous_findings": [finding],
            "bridge": {"executed": executed, "unsupported": unsupported},
        },
    )


def validate_scene_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    if "asset_triage" in payload:
        from . import asset_triage

        errors.extend(
            f"asset_triage.{error}"
            for error in asset_triage.validate_payload_block(payload["asset_triage"])
        )
    if payload.get("contract_version") != contract.VERSION:
        errors.append(
            f"contract_version must be {contract.VERSION!r}, "
            f"got {payload.get('contract_version')!r}"
        )
    context = payload.get("execution_context")
    if not isinstance(context, dict):
        errors.append("execution_context missing")
        return errors
    if context.get("mode") not in ("strict", "autonomous"):
        errors.append(f"mode {context.get('mode')!r} not in strict/autonomous")
    score = context.get("specificity_score")
    if not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
        errors.append(f"specificity_score {score!r} outside 0..1")
    rationale = context.get("creative_rationale")
    if not isinstance(rationale, dict):
        errors.append("creative_rationale missing")
        return errors
    for key in ("pacing", "color_palette", "typography"):
        block = rationale.get(key)
        if not isinstance(block, dict) or not str(block.get("rationale") or "").strip():
            errors.append(f"creative_rationale.{key} has no rationale")
    for key in ("rd_search_required", "rd_search_conducted"):
        if not isinstance(rationale.get(key), bool):
            errors.append(f"creative_rationale.{key} must be boolean")
    sources = rationale.get("rd_search_sources")
    if not isinstance(sources, list):
        errors.append("creative_rationale.rd_search_sources must be a list")
    elif rationale.get("rd_search_conducted") and not sources:
        errors.append("rd_search_conducted true but rd_search_sources is empty")
    scene = payload.get("scene")
    if not isinstance(scene, dict):
        errors.append("scene missing")
        return errors
    if scene.get("aspect_ratio") not in ("9:16", "16:9", "1:1", "4:5"):
        errors.append(f"scene.aspect_ratio {scene.get('aspect_ratio')!r} invalid")
    resolution = scene.get("resolution")
    if not (
        isinstance(resolution, list)
        and len(resolution) == 2
        and all(isinstance(v, int) and v > 0 for v in resolution)
    ):
        errors.append(f"scene.resolution {resolution!r} invalid")
    if scene.get("engine") not in ("pillow", "chromium"):
        errors.append(f"scene.engine {scene.get('engine')!r} invalid")
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="vx.decide")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--assets", default="")
    args = parser.parse_args(argv)
    assets = [item.strip() for item in args.assets.split(",") if item.strip()] or None
    try:
        payload = parse_and_decide(args.prompt, assets=assets)
    except AmbiguousContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return contract.EXIT_NEEDS_INPUT
    errors = validate_scene_payload(payload)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return contract.EXIT_USAGE
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return contract.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
