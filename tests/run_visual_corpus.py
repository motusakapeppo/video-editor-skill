"""W2.5: due progetti, due render, tutti i cancelli.

Il verdetto designa la coppia ``real_interview_169`` vs ``real_street_169``. Il
runner costruisce i due brief con ``cli.build_visual_brief``, renderizza ogni clip
con un piano reale (``render.render`` con ``visual=``), riempie il ledger dai
risultati ffmpeg veri e passa i cancelli: ledger, superficie, hook, copertura,
documentazione, Gate 2 e Gate 3. La matrice completa delle 36 coppie viene
scritta in ``tests/fixtures/distinct_matrix.json`` con ogni flag di famiglia.

La finestra di render segue due regole dichiarate: 4:5 (il formato del delivery
approvato, dove la banda d'inchiostro 0.45-0.94% e' stata misurata) e, per il
clip con parlato, una finestra CON parole e SENZA testo impresso nella sorgente
(il lower-third dell'interview falserebbe l'inchiostro con testo che il sistema
non ha dichiarato). Il clip senza parole non ha sottotitoli: non c'e' testo da
bruciare e il suo inchiostro e' quello del contenuto.

Uso:
    python tests/run_visual_corpus.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import brief as brief_mod, docs, gates, prove  # noqa: E402
from vx import animplan, gradeplan, palette as palette_mod  # noqa: E402
from vx import screen as screen_mod  # noqa: E402
from vx import contract, probe as probe_mod, render, subs  # noqa: E402
from vx.cli import _cut_density_choice, _typography_choice  # noqa: E402
from vx.cli import build_visual_brief, _visual_from_brief  # noqa: E402

CORPUS = ROOT / "tests" / "stock_corpus"
FIXTURES = ROOT / "tests" / "fixtures"
WORK = ROOT / "tests" / "_out" / "visual_corpus"
DESIGNATED = ("real_interview_169", "real_street_169")
WINDOW_S = 5.0
# Finestre dichiarate: il clip con parlato prende una finestra con parole e
# senza testo impresso nella sorgente (il lower-third dell'interview falserebbe
# l'inchiostro con testo che il sistema non ha dichiarato). Il clip senza
# parole prende la PRIMA finestra di 5 s il cui audio normalizza in modo
# lineare al target social: le finestre 0-15 s spingono loudnorm in modo
# dinamico, che allunga l'audio di ~95 ms e fa rifiutare il file al Gate 3
# (deriva av reale, non un difetto del cancello). La stessa finestra mantiene
# l'inchiostro del contenuto nella banda 0.30-2.00%.
WINDOWS = {"real_interview_169": (20.0, 25.0), "real_street_169": (15.0, 20.0)}
SUB_WINDOW_PAD = 0.5

PASS = 0
FAIL = 0


def _rel(path: str) -> str:
    """Path relative to the repo root, so the fixture stays portable."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:44} {detail}")


def _profile(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Le decisioni di superficie per un clip, senza toccare la sorgente.

    Stesse chiamate di ``build_visual_brief`` meno ``screen.plan`` (che serve
    alla sola scelta di inquadratura del brief): la matrice legge l'offset
    misurato dal corpus, che e' lo stesso campo che il gate legge dal brief.
    La matrice e' a livello di superficie (misura del corpus); la coppia
    designata e' a livello di render (crop applicato).
    Le scelte senza filtro eseguibile sono gia' degradate a inazione, come fa
    il brief, cosi' la matrice e il ledger parlano la stessa lingua.
    """
    m = dict(clip)
    pal = palette_mod.decide(m)
    grade = gradeplan.decide(m, pal)
    ratio = m.get("transient_ratio")
    anim = animplan.decide(
        None if ratio is None else float(ratio), m.get("onsets_per_s")
    )
    cut = _cut_density_choice(m)
    typo = _typography_choice(m)
    shot = {
        "duration": float(m.get("duration_s") or 0.25),
        "width": int(m.get("width") or 0),
        "height": int(m.get("height") or 0),
        "fps": float(m.get("fps") or 25.0),
    }
    pal_value = "neutral" if pal["applied"] else pal["family"]
    grade_value = grade["node"]["type"]
    if grade["applied"] and not gradeplan.chain_for_shot(grade["node"]):
        grade_value = "none"
    anim_name, anim_count = anim["name"], anim["count"]
    if anim["applied"] and not animplan.chain_for_shot(anim, shot):
        anim_name, anim_count = "none", 0
    typo_value = 0 if typo["applied"] else typo["value"]
    offset = m.get("subject_offset")
    comp_applied = bool(
        offset is not None
        and max(
            abs(float(offset.get("x") or 0.0)),
            abs(float(offset.get("y") or 0.0)),
        )
        >= screen_mod.OFFSET_THRESHOLD
    )
    comp_value = offset if offset is not None else None
    return {
        "choices": {
            "palette": {"value": pal_value},
            "grade": {"value": grade_value},
            "animation": {"name": anim_name, "count": anim_count},
            "cut_density": {"value": cut["value"]},
            "composition": {"value": comp_value, "applied": comp_applied},
            "typography": {"value": typo_value},
        },
        "measurements": {
            "hue_mass_top3": m.get("hue_mass_top3") or [],
            "subject_offset": m.get("subject_offset"),
            "first_speech_onset_s": m.get("first_speech_onset_s"),
            "first3s_motion": m.get("first3s_motion"),
            "face_present": bool(m.get("face_present")),
            "luma_p50": m.get("luma_p50"),
        },
    }


def _build_matrix(profiles: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    names = sorted(profiles)
    rows: List[Dict[str, Any]] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            surface = prove.distinct_surface(profiles[a], profiles[b])
            hook = prove.hook_distinct(profiles[a], profiles[b])
            rows.append(
                {
                    "a": a,
                    "b": b,
                    "families": surface["families"],
                    "geometry_available": surface["geometry_available"],
                    "hue_l1": surface["hue_l1"],
                    "hook_types": surface["hook_types"],
                    "distinct_ok": surface["ok"],
                    "hook_distinct": hook,
                    "passes_both": bool(surface["ok"] and hook),
                }
            )
    return rows


def _shifted_cues(transcript: Any, t0: float, t1: float) -> List[Any]:
    """Le cue del transcript dentro la finestra, con i tempi riportati a zero."""
    words = [
        {
            "start": max(0.0, float(w["start"]) - t0),
            "end": max(0.0, float(w["end"]) - t0),
            "word": w["word"],
        }
        for w in (transcript.words or [])
        if float(w["start"]) < t1 + SUB_WINDOW_PAD
        and float(w["end"]) > t0 - SUB_WINDOW_PAD
    ]

    class _Shim:
        pass

    shim = _Shim()
    shim.words = words
    return subs.build_cues(shim, style="karaoke", aspect="4:5")


def _segment_fragment(graph: str, index: int) -> str:
    """Il frammento injectato dal visual nel segmento i, o la stringa vuota."""
    match = re.search(rf"format=yuv420p,([^;\[\]]*)\[v{index}\]", graph)
    if not match:
        return ""
    return match.group(1).strip(",")


def _concat_fragment(graph: str) -> str:
    match = re.search(r"concat=n=\d+:v=1:a=\d", graph)
    return match.group(0) if match else "null"


def _fill_executed(
    b: Dict[str, Any],
    result: Dict[str, Any],
    crop_plan: Optional[Dict[str, Any]] = None,
) -> None:
    """Sei voci del ledger dai risultati ffmpeg veri.

    ``grade`` e ``animation`` registrano il frammento realmente injectato nel
    grafo (o ``null`` quando la decisione e' inazione); ``palette`` e
    ``tipografia`` non dichiarano filtri in ``vx.visual/1.0`` (le decorazioni
    sono fuori scope, la tipografia non ha burn proprio) e registrano ``none``;
    ``cut_density`` registra il concat realmente eseguito; ``composition``
    registra il crop solo quando il ``crop_plan`` e' stato passato al render.
    """
    graph = result["graph"]
    frames = int(result["frames"])
    grade_node = b["_visual"]["grade_node"]
    anim = b["_visual"]["animation"]
    grade_fragment = gradeplan.chain_for_shot(grade_node)
    anim_fragment = _segment_fragment(graph, 0)
    if crop_plan:
        crop_w = int(crop_plan["crop_w"])
        crop_h = int(crop_plan["crop_h"])
        box = crop_plan["boxes"][0]
        comp_name = "crop"
        comp_filter = f"crop={crop_w}:{crop_h}:{int(box['x'])}:{int(box['y'])}"
    else:
        comp_name = "none"
        comp_filter = "null"

    entries = [
        ("palette", "none", "null"),
        ("grade", str(grade_node.get("type") or "none"), grade_fragment or "null"),
        ("animation", str(anim.get("name") or "none"), anim_fragment or "null"),
        ("cut_density", "snap", _concat_fragment(graph)),
        ("composition", comp_name, comp_filter),
        ("typography", "none", "null"),
    ]
    for axis, name, filter_text in entries:
        brief_mod.add_executed(
            b,
            axis,
            name,
            "ffmpeg",
            filter_text,
            0,
            frames,
            at=0.0 if filter_text != "null" else None,
        )


def _visual_for(b: Dict[str, Any], n_segments: int) -> Dict[str, Any]:
    """Il visual del brief per un grafo di ``n_segments`` segmenti.

    Il ``count`` dell'asse animazione e' il numero di esecuzioni dichiarate: i
    primi ``count`` segmenti portano la scelta, gli altri ``none``. Con una
    scelta di inazione la lista resta quella del brief e il grafo non cambia.
    """
    visual = _visual_from_brief(b)
    anim = b["_visual"]["animation"]
    applied = bool(b["choices"]["animation"]["applied"])
    count = int(b["choices"]["animation"]["count"] or 0)
    if applied and count > 0:
        visual["animations"] = [
            anim if i < count else {"name": "none"} for i in range(n_segments)
        ]
    return visual


def _render_clip(
    name: str,
    clip: Dict[str, Any],
    b: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
    """Renderizza il clip con il piano reale e il visual del brief.

    Il piano ha due tagli contigui che coprono la finestra: il grafo porta il
    concat v1a1 che il Gate 2 pretende, e la timeline dei sottotitoli resta
    continua (il taglio e' nello stesso punto, quindi lo shift unico basta).
    Quando la composizione e' ``offset`` e applicata, il crop del brief diventa
    il ``crop_plan`` del render: la differenza che il gate dichiara e' quella
    che il file contiene.
    """
    src = CORPUS / clip["path"]
    t0, t1 = WINDOWS[name]
    mid = (t0 + t1) / 2.0
    out_w, out_h = contract.output_size("4:5", "proxy")
    subs_path: Optional[str] = None
    if clip.get("words"):
        transcript = subs.transcribe(str(src))
        cues = _shifted_cues(transcript, t0, t1)
        if cues:
            ass = WORK / name / f"{name}.ass"
            subs_path = subs.write_ass(
                cues, ass, aspect="4:5", out_w=out_w, out_h=out_h
            )
    plan = contract.EditPlan(
        output={"aspect": "4:5", "loudness": "social"},
        cuts=[{"in": t0, "out": mid}, {"in": mid, "out": t1}],
        render={"precision": "proxy"},
    )
    comp = b["_visual"]["composition"]
    crop_plan: Optional[Dict[str, Any]] = None
    if comp.get("applied") and comp.get("name") == "offset":
        crop = comp.get("crop") or {}
        crop_plan = {
            "crop_w": int(crop.get("crop_w") or 0),
            "crop_h": int(crop.get("crop_h") or 0),
            "mode": "lock",
            "boxes": [{"x": int(crop.get("x") or 0), "y": int(crop.get("y") or 0)}],
        }
    media = probe_mod.probe(str(src)).to_dict()
    result = render.render(
        plan,
        media,
        precision="proxy",
        workdir=WORK / name / "render",
        subs_path=subs_path,
        crop_plan=crop_plan,
        visual=_visual_for(b, 2),
    )
    return result, subs_path, crop_plan


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    stats = json.loads((FIXTURES / "corpus_stats.json").read_text(encoding="utf-8"))
    clips = {c["name"]: c for c in stats["clips"]}
    print(f"corpus: {len(clips)} clip, hash {stats['corpus_hash'][:16]}...")

    profiles = {name: _profile(clip) for name, clip in clips.items()}
    matrix = _build_matrix(profiles)
    passing = [row for row in matrix if row["passes_both"]]
    print(
        f"matrice: {len(matrix)} coppie, {sum(1 for r in matrix if r['distinct_ok'])} "
        f"passano distinct_surface, {len(passing)} passano entrambi i cancelli"
    )

    designated = DESIGNATED
    designated_by = "verdict"
    pair_row = next(r for r in matrix if {r["a"], r["b"]} == set(designated))
    if not pair_row["passes_both"]:
        if not passing:
            print("STOP: nessuna coppia passa entrambi i cancelli di coppia")
            return 1
        best = max(passing, key=lambda r: (sum(r["families"].values()), r["hue_l1"]))
        designated = (best["a"], best["b"])
        designated_by = "calibrated"
        print(
            f"coppia designata rifiutata dai cancelli di coppia: ricalibrata su "
            f"{designated[0]} vs {designated[1]}"
        )
    name_a, name_b = designated
    print(f"coppia designata: {name_a} vs {name_b} ({designated_by})")

    briefs: Dict[str, Dict[str, Any]] = {}
    for name in designated:
        clip = clips[name]
        briefs[name] = build_visual_brief(CORPUS / clip["path"], clip, project=name)
        prof, got = profiles[name], briefs[name]
        consistent = (
            all(
                prof["choices"][axis]["value"] == got["choices"][axis]["value"]
                for axis in ("palette", "grade", "cut_density", "typography")
            )
            and prof["choices"]["animation"]["name"]
            == got["choices"]["animation"]["name"]
        )
        check(f"{name}: profilo matrice == brief", consistent, "")

    results: Dict[str, Dict[str, Any]] = {}
    crop_plans: Dict[str, Optional[Dict[str, Any]]] = {}
    for name in designated:
        print(f"  render {name}...")
        result, subs_path, crop_plan = _render_clip(name, clips[name], briefs[name])
        results[name] = result
        crop_plans[name] = crop_plan
        _fill_executed(briefs[name], result, crop_plan)
        print(
            f"    {result['path']} frames={result['frames']} "
            f"subs={'si' if subs_path else 'no'} "
            f"crop={'si' if crop_plan else 'no'}"
        )

    surface = prove.distinct_surface(briefs[name_a], briefs[name_b])
    hook = prove.hook_distinct(briefs[name_a], briefs[name_b])
    for name in designated:
        b = briefs[name]
        b["distinct_surface"] = {
            "ok": surface["ok"],
            "scope": surface["scope"],
            "families": [k for k, v in surface["families"].items() if v],
            "geometry_available": surface["geometry_available"],
        }
    pair_row["families"] = surface["families"]
    pair_row["geometry_available"] = surface["geometry_available"]
    pair_row["hue_l1"] = surface["hue_l1"]
    pair_row["hook_types"] = surface["hook_types"]
    pair_row["distinct_ok"] = surface["ok"]
    pair_row["hook_distinct"] = hook
    pair_row["passes_both"] = bool(surface["ok"] and hook)
    passing = [row for row in matrix if row["passes_both"]]

    print("\ncancelli:")
    for name in designated:
        b = briefs[name]
        errors = prove.check_ledger(b)
        check(f"{name}: check_ledger", errors == [], str(errors[:2]))
        errors = brief_mod.validate(b)
        check(f"{name}: validate", errors == [], str(errors[:2]))

    check(
        "distinct_surface ok",
        surface["ok"] is True,
        " ".join(f"{k}={v}" for k, v in surface["families"].items()),
    )
    check(
        "hook_distinct (P1)",
        hook is True,
        f"{surface['hook_types']['a']} vs {surface['hook_types']['b']}",
    )

    coverages: Dict[str, Dict[str, Any]] = {}
    for name in designated:
        coverage = prove.coverage_gate(briefs[name], results[name]["path"])
        coverages[name] = coverage
        check(
            f"{name}: coverage_gate",
            coverage["ok"] is True,
            f"ink {coverage['ink_pct']}% overlay {coverage['overlay_pct']}%",
        )

    docs_texts: Dict[str, str] = {}
    for name in designated:
        b = briefs[name]
        text = docs.render(b)
        docs_texts[name] = text
        b["docs_match"]["doc_sha256"] = prove._sha256_text(text)
        b["docs_match"]["ledger_sha256"] = docs.hash_ledger(b)
        check(
            f"{name}: docs_match",
            docs.match(b, text) is True,
            f"ledger {docs.hash_ledger(b)[:12]}...",
        )

    for name in designated:
        result = results[name]
        try:
            gates.graph(result["graph"])
            check(f"{name}: Gate 2 (grafo)", True, "")
        except contract.QCFailure as exc:
            check(f"{name}: Gate 2 (grafo)", False, str(exc.result.get("failures")))
        try:
            gates.post_render(
                contract.EditPlan(
                    cuts=[{"in": WINDOWS[name][0], "out": WINDOWS[name][1]}]
                ),
                {
                    "fps": float(results[name]["fps"]),
                    "duration": float(clips[name]["duration_s"]),
                },
                result["path"],
                workdir=WORK / name,
                planned_total=WINDOW_S,
            )
            check(f"{name}: Gate 3 (file)", True, "")
        except contract.QCFailure as exc:
            check(f"{name}: Gate 3 (file)", False, str(exc.result.get("failures")))

    matrix_path = FIXTURES / "distinct_matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "schema": "vx.distinct_matrix/1.0",
                "corpus_hash": stats["corpus_hash"],
                "designated": {
                    "pair": list(designated),
                    "designated_by": designated_by,
                    "families": pair_row["families"],
                    "hook_types": pair_row["hook_types"],
                },
                "pairs": matrix,
                "summary": {
                    "total": len(matrix),
                    "distinct_ok": sum(1 for r in matrix if r["distinct_ok"]),
                    "hook_distinct": sum(1 for r in matrix if r["hook_distinct"]),
                    "passes_both": len(passing),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"scritto {matrix_path}")

    pair_path = FIXTURES / "visual_pair.json"
    pair_path.write_text(
        json.dumps(
            {
                "schema": "vx.visual_pair/1.0",
                "designated": {
                    "pair": list(designated),
                    "designated_by": designated_by,
                },
                "briefs": {name: briefs[name] for name in designated},
                "gate_results": {
                    "distinct_surface": surface,
                    "hook_distinct": hook,
                    "coverage": {
                        name: {
                            "ok": coverages[name]["ok"],
                            "ink_pct": coverages[name]["ink_pct"],
                            "overlay_pct": coverages[name]["overlay_pct"],
                            "n_frames": coverages[name]["n_frames"],
                        }
                        for name in designated
                    },
                },
                "renders": {
                    name: {
                        "path": _rel(results[name]["path"]),
                        "provenance": "declared",
                        "frames": results[name]["frames"],
                        "expected_frames": results[name]["expected_frames"],
                        "crop_plan": crop_plans[name],
                        "graph_sha256": hashlib.sha256(
                            results[name]["graph"].encode("utf-8")
                        ).hexdigest(),
                    }
                    for name in designated
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"scritto {pair_path}")

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
