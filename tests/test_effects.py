"""The composable effect language -- spec, validation, cost, expansion.

    python tests/test_effects.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass. No network, no ffmpeg, no render: this module only describes.

What it proves:

a. a valid spec validates clean, and every one of the eight primitives refuses a
   parameter outside its declared domain with the field path in the message;
b. types, enums, colours, spans, unknown params, unknown kinds and structural
   errors are refused with a path, never with a traceback;
c. curves: linear/in/out/in_out/bezier evaluate as declared, bezier needs
   points, keys are ordered and multiple stops are honoured;
d. cost is additive and monotone (one < two < three primitives), never exceeds
   10, and a declared cost is a floor;
e. expand() turns the historical names -- ``zoom``, ``lut warm``, ``shake`` --
   into the same shape a hand-written spec has, and supports overrides;
f. transitions are formulas: every entry validates and stays inside budget.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx.effects import (  # noqa: E402
    CURVES,
    HISTORICAL_NAMES,
    MAX_COST,
    PRIMITIVES,
    TRANSITIONS,
    SpecError,
    cost,
    describe,
    describe_primitive,
    expand,
    normalize,
    sample_curve,
    sample_param,
    transition,
    validate,
)

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def issues(spec):
    return validate(spec)


def first(spec):
    found = validate(spec)
    return found[0] if found else None


def one(kind: str, params: dict | None = None, **extra) -> dict:
    primitive = {"kind": kind, "params": dict(params or {})}
    primitive.update(extra)
    return primitive


def wrap(*primitives, duration: float = 2.4) -> dict:
    return {"id": "prova", "primitives": list(primitives), "duration": duration}


VALID = {
    "id": "spinta_calda",
    "primitives": [
        {
            "kind": "transform",
            "params": {"scale": 1.15, "x": [0, 40], "y": 0},
            "curve": {"easing": "out", "from": 0.0, "to": 1.0},
        },
        {"kind": "grade", "params": {"lut": "warm", "contrast": 1.1}},
        {"kind": "mask", "params": {"shape": "ellipse", "feather": 24}},
    ],
    "duration": 2.4,
    "cost": 1.0,
}


def main() -> int:
    print("\nE1 -- una specifica valida e le otto primitive")
    found = issues(VALID)
    check("E1 specifica valida: nessun errore", found == [], found)
    check(
        "E1 le primitive dichiarate sono otto",
        set(PRIMITIVES) == {
            "transform",
            "grade",
            "blur",
            "mask",
            "blend",
            "time",
            "text",
            "particles",
        },
        sorted(PRIMITIVES),
    )
    check(
        "E1 ogni primitiva ha almeno un parametro con default",
        all(decl.params for decl in PRIMITIVES.values())
        and all(
            any(hasattr(param, "default") for param in decl.params.values())
            for decl in PRIMITIVES.values()
        ),
    )
    described = describe()
    check(
        "E1 describe() elenca cinque curve e tutte le transizioni",
        len(described["curves"]) == 5 and described["transitions"] == sorted(TRANSITIONS),
        described["curves"],
    )
    check(
        "E1 describe_primitive('grade') espone range e default",
        describe_primitive("grade")["params"]["contrast"]["min"] == 0.5
        and describe_primitive("grade")["params"]["contrast"]["default"] == 1.0,
    )

    print("\nE2 -- un parametro fuori range per ogni primitiva")
    cases = [
        ("transform", {"scale": 4.0}, "primitives[0].params.scale"),
        ("grade", {"contrast": 3.0}, "primitives[0].params.contrast"),
        ("blur", {"sigma": -1.0}, "primitives[0].params.sigma"),
        ("mask", {"feather": 500.0}, "primitives[0].params.feather"),
        ("blend", {"mix": 1.5}, "primitives[0].params.mix"),
        ("time", {"rate": 0.0}, "primitives[0].params.rate"),
        ("text", {"size": 9000.0}, "primitives[0].params.size"),
        ("particles", {"count": 0}, "primitives[0].params.count"),
    ]
    for kind, params, where in cases:
        issue = first(wrap(one(kind, params)))
        check(
            f"E2 {kind}: {list(params)[0]} fuori range rifiutato con il percorso",
            issue is not None and issue.where == where,
            "" if issue is None else issue.message,
        )
    example = first(wrap(one("transform"), one("grade", {"contrast": 3.0})))
    check(
        "E2 il percorso usa l'indice della primitiva (primitives[1]...)",
        example is not None and example.where == "primitives[1].params.contrast",
    )
    check(
        "E2 il messaggio e' in italiano e nomina l'intervallo",
        example is not None
        and "deve stare in [0.5, 2.0]" in example.message,
        "" if example is None else example.message,
    )
    span = first(wrap(one("transform", {"x": [0, 5000]})))
    check(
        "E2 una coppia animata nomina l'estremo fuori range",
        span is not None and span.where == "primitives[0].params.x[1]",
        "" if span is None else span.message,
    )
    check(
        "E2 una coppia di lunghezza sbagliata e' rifiutata",
        bool(issues(wrap(one("transform", {"scale": [1.0, 1.1, 1.2]})))),
    )

    print("\nE3 -- tipi, enum, colori, campi e struttura")
    check(
        "E3 un numero scritto come stringa e' rifiutato",
        "deve essere un numero" in (first(wrap(one("grade", {"contrast": "1.1"}))).message or ""),
    )
    check(
        "E3 un lut fuori elenco e' rifiutato",
        first(wrap(one("grade", {"lut": "sunset"}))).where == "primitives[0].params.lut",
    )
    check(
        "E3 un colore non #RRGGBB e' rifiutato",
        first(wrap(one("text", {"color": "bianco"}))).where
        == "primitives[0].params.color",
    )
    check(
        "E3 un parametro sconosciuto e' rifiutato con la sua primitiva",
        first(wrap(one("transform", {"glow": 1.0}))).where
        == "primitives[0].params.glow",
    )
    check(
        "E3 una primitiva sconosciuta e' rifiutata con l'elenco delle ammesse",
        "primitiva sconosciuta" in (first(wrap(one("sparkle"))).message or ""),
    )
    check(
        "E3 primitives vuoto e' rifiutato",
        first(wrap()).where == "primitives",
    )
    check(
        "E3 duration non positiva e' rifiutata",
        first({**wrap(one("blur")), "duration": 0}).where == "duration",
    )
    check(
        "E3 un campo sconosciuto in cima e' rifiutato",
        first({**wrap(one("blur")), "speed": 2}).where == "speed",
    )
    check(
        "E3 un campo sconosciuto nella primitiva e' rifiutato",
        first(wrap({**one("blur"), "phase": 2})).where == "primitives[0].phase",
    )
    check(
        "E3 una primitiva non oggetto e' rifiutata, non solleva",
        first(wrap("blur")).where == "primitives[0]",
    )
    check(
        "E3 validate() su una specifica non oggetto ritorna un errore",
        bool(issues(["non", "una", "specifica"])),
    )

    print("\nE4 -- curve dichiarate e chiavi multiple")
    check(
        "E4 una curva sconosciuta e' rifiutata sul campo easing",
        first(wrap(one("blur", curve={"easing": "bounce"}))).where
        == "primitives[0].curve.easing",
    )
    check(
        "E4 bezier senza punti e' rifiutata",
        first(wrap(one("blur", curve={"easing": "bezier"}))).where
        == "primitives[0].curve.points",
    )
    check(
        "E4 points con tre punti e' rifiutato",
        bool(issues(wrap(one("blur", curve={"easing": "bezier", "points": [[0, 0], [0.5, 1], [1, 1]]})))),
    )
    check(
        "E4 chiavi non crescenti sono rifiutate",
        first(
            wrap(
                one(
                    "blur",
                    curve={"keys": [{"t": 0.6, "value": 0}, {"t": 0.2, "value": 1}]},
                )
            )
        ).where
        == "primitives[0].curve.keys[1].t",
    )
    check(
        "E4 una sola chiave non e' una curva temporale",
        first(wrap(one("blur", curve={"keys": [{"t": 0, "value": 0}]}))).where
        == "primitives[0].curve.keys",
    )
    check(
        "E4 easing bezier dentro una chiave e' rifiutato (manca il punto)",
        first(
            wrap(
                one(
                    "blur",
                    curve={
                        "keys": [
                            {"t": 0, "value": 0, "easing": "bezier"},
                            {"t": 1, "value": 1},
                        ]
                    },
                )
            )
        ).where
        == "primitives[0].curve.keys[0].easing",
    )
    check(
        "E4 linear va da 0 a 1 passando per 0.5",
        [sample_curve({"easing": "linear"}, u) for u in (0.0, 0.5, 1.0)]
        == [0.0, 0.5, 1.0],
    )
    check(
        "E4 out e' piu' avanti di in a un quarto del tempo",
        sample_curve({"easing": "out"}, 0.25) > sample_curve({"easing": "in"}, 0.25),
        f"out={sample_curve({'easing': 'out'}, 0.25):.4f}",
    )
    check(
        "E4 in_out e' simmetrica (0.25/0.75 sommano a 1)",
        abs(
            sample_curve({"easing": "in_out"}, 0.25)
            + sample_curve({"easing": "in_out"}, 0.75)
            - 1.0
        )
        < 1e-9,
    )
    bezier_points = {"easing": "bezier", "points": [[0.42, 0.0], [0.58, 1.0]]}
    check(
        "E4 bezier passa per 0, 0.5 e 1",
        abs(sample_curve(bezier_points, 0.0)) < 1e-9
        and abs(sample_curve(bezier_points, 0.5) - 0.5) < 1e-3
        and abs(sample_curve(bezier_points, 1.0) - 1.0) < 1e-9,
        f"{sample_curve(bezier_points, 0.5):.4f}",
    )
    check(
        "E4 from/to spostano l'inviluppo (10 -> 20)",
        abs(sample_curve({"easing": "linear", "from": 10.0, "to": 20.0}, 0.5) - 15.0)
        < 1e-9,
    )
    keys = {
        "keys": [
            {"t": 0.0, "value": 0.0},
            {"t": 0.5, "value": 1.0},
            {"t": 1.0, "value": 0.0},
        ]
    }
    check(
        "E4 tre chiavi: 0.25 -> 0.5, 0.5 -> 1, 0.75 -> 0.5",
        abs(sample_curve(keys, 0.25) - 0.5) < 1e-9
        and abs(sample_curve(keys, 0.5) - 1.0) < 1e-9
        and abs(sample_curve(keys, 0.75) - 0.5) < 1e-9,
    )
    check(
        "E4 sample_param interpola una coppia con la curva",
        abs(
            sample_param(
                one("transform", {"scale": [1.0, 1.15]}, curve={"easing": "out"}),
                "scale",
                0.5,
            )
            - 1.1125
        )
        < 1e-9,
    )
    check(
        "E4 sample_param lascia statico un valore singolo",
        sample_param(one("transform", {"opacity": 0.4}), "opacity", 0.9) == 0.4,
    )

    print("\nE5 -- costo additivo, monotono, dentro il budget")
    c1 = cost(wrap(one("transform")))
    c2 = cost(wrap(one("transform"), one("grade")))
    c3 = cost(wrap(one("transform"), one("grade"), one("text")))
    check(
        "E5 una < due < tre primitive",
        c1 < c2 < c3,
        f"{c1} < {c2} < {c3}",
    )
    check(
        "E5 aggiungere particelle costa piu' che aggiungere testo",
        cost(wrap(one("particles", {"count": 2000}))) - cost(wrap(one("transform")))
        > cost(wrap(one("text"))) - cost(wrap(one("transform"))),
    )
    heavy = wrap(*[one("particles", {"count": 5000, "size": 64}) for _ in range(16)], duration=60.0)
    check(
        "E5 il costo e' normalizzato: mai oltre 10",
        0.0 <= cost(VALID) <= MAX_COST and cost(heavy) == MAX_COST,
        f"valido={cost(VALID)} pesante={cost(heavy)}",
    )
    check(
        "E5 un cost dichiarato e' un minimo, non un ribasso",
        cost({**wrap(one("transform")), "cost": 4.0}) == 4.0
        and cost({**wrap(one("transform")), "cost": 0.1}) == cost(wrap(one("transform"))),
    )
    check(
        "E5 il costo di una transizione resta sotto il budget",
        all(cost(transition(name)) < MAX_COST for name in TRANSITIONS),
    )

    print("\nE6 -- expand(): i nomi storici convivono con le specifiche")
    zoom = expand("zoom")
    check(
        "E6 'zoom' diventa transform scale [1.0, 1.15] su 2.4s",
        zoom["primitives"][0]["kind"] == "transform"
        and zoom["primitives"][0]["params"]["scale"] == [1.0, 1.15]
        and zoom["duration"] == 2.4,
    )
    check(
        "E6 'lut warm' diventa grade lut=warm",
        expand("lut warm")["primitives"][0]["params"]["lut"] == "warm",
    )
    check(
        "E6 ogni nome storico produce una specifica valida",
        all(validate(expand(name)) == [] for name in HISTORICAL_NAMES),
        f"{len(HISTORICAL_NAMES)} nomi",
    )
    check(
        "E6 ogni nome storico e' dentro il budget",
        all(cost(expand(name)) <= MAX_COST for name in HISTORICAL_NAMES),
    )
    check(
        "E6 'shake' compone due primitive (x e y) con chiavi",
        len(expand("shake")["primitives"]) == 2
        and "keys" in expand("shake")["primitives"][0]["curve"],
    )
    zoomed = expand("zoom scale=1.3")
    check(
        "E6 override numerico: 'zoom scale=1.3'",
        zoomed["primitives"][0]["params"]["scale"] == 1.3,
    )
    warm = expand("lut warm contrast=1.2")
    check(
        "E6 override su un nome di due parole",
        warm["primitives"][0]["params"]["contrast"] == 1.2,
    )
    try:
        expand("sparkle")
        unknown = None
    except SpecError as exc:
        unknown = str(exc)
    check(
        "E6 un nome sconosciuto solleva con un suggerimento",
        unknown is not None and "sconosciuto" in unknown,
        unknown,
    )
    try:
        expand("shak")
        near = ""
    except SpecError as exc:
        near = str(exc)
    check("E6 un nome quasi giusto suggerisce 'shake'", "shake" in near, near)
    source = copy.deepcopy(VALID)
    normalised = expand(source)
    check(
        "E6 expand(spec) riempie i default senza mutare l'originale",
        normalised["primitives"][2]["params"]["cx"] == 0.0
        and "cx" not in source["primitives"][2]["params"],
    )
    check(
        "E6 expand due volte e' deterministico",
        expand("zoom") == expand("zoom"),
    )

    print("\nE7 -- transizioni come formule")
    cross = transition("crossfade")
    check(
        "E7 crossfade e' un blend 0 -> 1 con curva in_out",
        cross["primitives"][0]["kind"] == "blend"
        and cross["primitives"][0]["params"]["mix"] == [0.0, 1.0]
        and cross["primitives"][0]["curve"]["easing"] == "in_out",
    )
    check(
        "E7 ogni formula di transizione valida",
        all(validate(transition(name)) == [] for name in TRANSITIONS),
        sorted(TRANSITIONS),
    )
    check(
        "E7 whip e glitch_cut compongono piu' primitive",
        len(transition("whip")["primitives"]) == 2
        and len(transition("glitch_cut")["primitives"]) == 2,
    )
    try:
        transition("morph")
        missing = ""
    except SpecError as exc:
        missing = str(exc)
    check("E7 una transizione sconosciuta solleva", "sconosciuta" in missing, missing)

    print("\nE8 -- il pacchetto espone la stessa API della specifica")
    import vx.effects as package

    check(
        "E8 vx.effects.validate e' vx.effects.compose.validate",
        package.validate is validate,
    )
    check(
        "E8 normalize() e' idempotente",
        normalize(normalize(VALID)) == normalize(VALID),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
