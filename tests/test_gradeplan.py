"""Test del grade plan: la luma misurata decide il nodo, non il gusto.

Le asserzioni sono sul contratto: driver, valore, provenienza, confidenza,
inazione. La regressione sul Gate 2 verifica che il frammento prodotto da
``chain_for_shot`` possa essere iniettato in un grafo EDL senza rompere il
cancello statico.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, edl as edl_mod, gates, grade, gradeplan, registry  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def main() -> int:
    thresholds = registry.THRESHOLDS["grade"]
    print(
        f"soglie grade: p33={thresholds['p33']} p67={thresholds['p67']} "
        f"({thresholds['provenance']})"
    )
    print(f"soglia cast: {gradeplan.CAST_THRESHOLD} ({gradeplan.CAST_PROVENANCE})")

    print("\nscuro: alza le ombre")
    dark = gradeplan.decide({"luma_p50": 0.10})
    check("nodo curve", dark["node"]["type"] == "curve", str(dark["node"]["type"]))
    check("master presente", bool(dark["node"]["master"]), str(dark["node"]["master"]))
    check("driver luma_p50", dark["driver"] == "luma_p50", dark["driver"])
    check("valore = luma", dark["value"] == 0.10, str(dark["value"]))
    check(
        "provenienza corpus_hash",
        dark["provenance"].startswith("corpus_hash:"),
        dark["provenance"][:24],
    )
    check("applied True", dark["applied"] is True, f"conf {dark['confidence']}")
    check(
        "confidenza fuori incertezza", dark["confidence"] > 0.8, str(dark["confidence"])
    )

    print("\nchiaro: abbassa le alte luci")
    bright = gradeplan.decide({"luma_p50": 0.90})
    check("nodo curve", bright["node"]["type"] == "curve", str(bright["node"]["type"]))
    check(
        "master presente", bool(bright["node"]["master"]), str(bright["node"]["master"])
    )
    check("applied True", bright["applied"] is True, f"conf {bright['confidence']}")

    print("\nmedio: nessuna correzione")
    mid = gradeplan.decide({"luma_p50": 0.40})
    check("nodo none", mid["node"] == {"type": "none"}, str(mid["node"]))
    check(
        "applied True (decisione sicura)",
        mid["applied"] is True,
        f"conf {mid['confidence']}",
    )

    print("\nincerto: inazione")
    uncertain = gradeplan.decide({"luma_p50": 0.35})
    check("nodo none", uncertain["node"] == {"type": "none"}, str(uncertain["node"]))
    check(
        "applied False",
        uncertain["applied"] is False,
        f"conf {uncertain['confidence']}",
    )
    check(
        "confidenza in [0.2, 0.8]",
        0.2 <= uncertain["confidence"] <= 0.8,
        str(uncertain["confidence"]),
    )
    check("frase di inazione", bool(uncertain["inaction"]), uncertain["inaction"])

    print("\nluma non misurata: inazione")
    missing = gradeplan.decide({"luma_p50": None})
    check("nodo none", missing["node"] == {"type": "none"}, str(missing["node"]))
    check("applied False", missing["applied"] is False, str(missing["applied"]))
    check("valore None", missing["value"] is None, str(missing["value"]))

    print("\ncast oltre soglia con luma medio: neutralizza i canali")
    cast = gradeplan.decide({"luma_p50": 0.40, "cast_rg": 0.20, "cast_by": -0.15})
    check("nodo curve", cast["node"]["type"] == "curve", str(cast["node"]["type"]))
    check(
        "canali r e b",
        set(cast["node"]["channels"]) == {"r", "b"},
        str(sorted(cast["node"]["channels"])),
    )
    check("nessun master", not cast["node"]["master"], str(cast["node"]["master"]))
    check("driver cast_rg", cast["driver"] == "cast_rg", cast["driver"])
    check("applied True", cast["applied"] is True, f"conf {cast['confidence']}")

    print("\nluma scuro + cast: master e canali insieme")
    combined = gradeplan.decide({"luma_p50": 0.10, "cast_rg": 0.20, "cast_by": -0.15})
    check("master presente", bool(combined["node"]["master"]), "master")
    check(
        "canali r e b",
        set(combined["node"]["channels"]) == {"r", "b"},
        str(sorted(combined["node"]["channels"])),
    )
    check("driver luma_p50", combined["driver"] == "luma_p50", combined["driver"])

    print("\ncast sotto soglia: nessun canale")
    clean = gradeplan.decide({"luma_p50": 0.10, "cast_rg": 0.02, "cast_by": -0.03})
    check(
        "nessun canale",
        clean["node"]["type"] == "curve" and not clean["node"]["channels"],
        str(clean["node"].get("channels")),
    )

    print("\nchain_for_shot")
    fragment = gradeplan.chain_for_shot(dark["node"])
    check("frammento non vuoto", bool(fragment), fragment[:48])
    check(
        "nessuna virgola ai bordi",
        not fragment.startswith(",") and not fragment.endswith(","),
        f"{fragment[:1]}...{fragment[-1:]}",
    )
    check(
        "uguale a grade.ffmpeg_filter",
        fragment == grade.ffmpeg_filter(grade.parse(dark["node"])),
        "",
    )
    check("none -> stringa vuota", gradeplan.chain_for_shot({"type": "none"}) == "", "")
    check(
        "giuntabile con virgola",
        f"scale=1080:1440,{fragment}".count(",,") == 0,
        "nessuna doppia virgola",
    )

    print("\nregressione Gate 2: chain iniettata nel grafo EDL")
    shots = [
        edl_mod.EdlShot(
            label="a",
            src_in=0.0,
            src_out=2.0,
            duration=2.0,
            t_in=0.0,
            t_out=2.0,
            frames=50,
            cut_on="bar",
            contrast=1.5,
        ),
        edl_mod.EdlShot(
            label="b",
            src_in=2.0,
            src_out=4.0,
            duration=2.0,
            t_in=2.0,
            t_out=4.0,
            frames=50,
            cut_on="bar",
            contrast=1.5,
        ),
    ]
    edit = edl_mod.Edl(
        fps=25.0,
        shots=shots,
        total=4.0,
        frames=100,
        rhythmic_shots=2,
        inaction_shots=0,
    )
    graph = edl_mod.filtergraph(
        edit,
        {"a": 0, "b": 1},
        fps=25.0,
        video_extra=lambda shot: gradeplan.chain_for_shot(dark["node"]),
    )
    check("chain presente nel grafo", "curves=m=" in graph, graph[:60])
    try:
        gates.graph(graph)
        check("Gate 2 accetta il grafo", True, "trim + atrim + concat")
    except contract.QCFailure as exc:
        check("Gate 2 accetta il grafo", False, str(exc.result.get("checks")))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
