"""F25-T15: il cancello di curatela — golden, parametri, linter, critico.

Scenario:

S1 — i sette template passano la curatela: golden ricostruito dal layout e misurato
     (ogni slot ha il colore dichiarato), record su disco con lo sha del golden,
     linter verde, parametri esposti; il validatore del registro riporta zero
     violazioni.
S2 — un template senza record e' rifiutato con il motivo e non entra nel conteggio.
S3 — se il golden cambia dopo la curatela, il record non vale piu' (sha diverso):
     il template e' rifiutato e il motivo dice che il golden e' cambiato.
S4 — una variante dello stesso disegno con un altro nome viene ammessa ma non
     conta: il numero dichiarato resta quello dei disegni.

    python tests/test_curation.py --write
    python tests/test_curation.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import curation, templates  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
REASON = "golden ricostruito dal layout e misurato: ogni slot mostra il colore dichiarato"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"    [SKIP] {label:62} {reason}")


def golden_path(name: str) -> Path:
    return ROOT / "tests" / "fixtures" / "templates" / f"{name}.png"


def record_path(name: str) -> Path:
    return ROOT / "tests" / "fixtures" / "templates" / f"{name}.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        skip("whole file", f"no imaging: {type(exc).__name__}")
        print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
        return 0

    names = sorted(templates.REGISTRY)

    if args.write:
        for name in names:
            template = templates.REGISTRY[name]
            golden = templates.render_golden(
                template, ratio=curation.GOLDEN_RATIO, size=curation.GOLDEN_SIZE, out=golden_path(name)
            )
            curation.approve(template, golden, record_path(name), why=REASON)
            curation.admit(template, record_path(name))

    # S1 -------------------------------------------------------------------- #
    check("S1 seven templates are declared", len(names) == 7, names)

    with tempfile.TemporaryDirectory(prefix="vx-curation-") as raw:
        work = Path(raw)
        for name in names:
            template = templates.REGISTRY[name]
            rebuilt = templates.render_golden(
                template,
                ratio=curation.GOLDEN_RATIO,
                size=curation.GOLDEN_SIZE,
                out=work / f"{name}.png",
            )
            if not args.write:
                check(
                    f"S1 {name}: golden, record, linter and critic agree",
                    curation.admit(template, record_path(name)) == "design",
                    name,
                )
                check(
                    f"S1 {name}: the golden on disk is the one the layout produces",
                    curation._sha(rebuilt) == curation._sha(golden_path(name)),
                    curation._sha(rebuilt)[:12],
                )

        check(
            "S1 the registry counts the seven designs",
            curation.count() == len(names) == 7,
            f"{curation.count()} disegni, {len(names)} template",
        )
        check(
            "S1 the registry validator reports zero violations",
            not curation.validate(),
            curation.validate(),
        )

        # S2 ---------------------------------------------------------------- #
        before = curation.count()
        orphan = templates.Template(
            name="orfano",
            family="title",
            slots=(templates.Slot("headline", "text", "headline"),),
            params=(templates.Param("tracking", 0.0, 0.2, 0.04),),
            layouts={"16:9": {"headline": (0.1, 0.4, 0.8, 0.2)}},
            why="prova: nessun record",
        )
        try:
            curation.admit(orphan, work / "assente.json")
            code, detail = "", ""
        except curation.CurationError as exc:
            code, detail = exc.code, exc.detail
        check("S2 an uncurated template is refused", code == "TEMPLATE_UNCURATED", code)
        check(
            "S2 and the reason is the missing record",
            "no curation record" in detail,
            detail[:60],
        )
        check(
            "S2 and the count does not move",
            curation.count() == before,
            curation.count(),
        )

        # S3 ---------------------------------------------------------------- #
        source = golden_path(names[0])
        tampered = work / "golden-manomesso.png"
        tampered.write_bytes(source.read_bytes() + b"\x00")
        record = json.loads(record_path(names[0]).read_text(encoding="utf-8"))
        # Il record punta al golden manomesso ma conserva lo sha di quello vero.
        record["golden"] = str(tampered)
        changed = work / "record.json"
        changed.write_text(json.dumps(record), encoding="utf-8")
        try:
            curation.admit(templates.REGISTRY[names[0]], changed)
            tamper_code, tamper_detail = "", ""
        except curation.CurationError as exc:
            tamper_code, tamper_detail = exc.code, exc.detail
        check(
            "S3 a golden changed after curation invalidates the record",
            tamper_code == "TEMPLATE_UNCURATED",
            tamper_code,
        )
        check(
            "S3 and the refusal says the golden changed",
            "golden changed" in tamper_detail,
            tamper_detail[:60],
        )

        # S4 ---------------------------------------------------------------- #
        base = templates.REGISTRY[names[0]]
        variant = templates.Template(
            name="variante_di_" + base.name,
            family=base.family,
            slots=base.slots,
            params=base.params,
            layouts=dict(base.layouts),
            why="stessa geometria, altro nome",
        )
        variant_golden = templates.render_golden(
            variant, ratio=curation.GOLDEN_RATIO, size=curation.GOLDEN_SIZE, out=work / "variante.png"
        )
        variant_record = work / "variante.json"
        curation.approve(variant, variant_golden, variant_record, why=REASON)
        before = curation.count()
        kind = curation.admit(variant, variant_record)
        check(
            "S4 a variant of the same design is admitted as a variant",
            kind == "variant" and variant.name in curation.VARIANTS,
            f"{kind} → {curation.VARIANTS.get(variant.name)}",
        )
        check(
            "S4 but it does not inflate the declared count",
            curation.count() == before,
            curation.count(),
        )
        check(
            "S4 the validator still reports no violation",
            not curation.validate(),
            curation.validate(),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
