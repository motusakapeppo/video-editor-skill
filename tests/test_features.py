"""vx.features: registry declares the defaults, unknown names fail, off means off."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import auto, cli, contract, features, settings  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def _fresh() -> None:
    settings.reset()


def _namespace(*extra: str):
    return cli.build_parser().parse_args(
        ["auto", "--prompt", "p", "--assets", "a", "--out", "o.json", *extra]
    )


def test_registry() -> None:
    check("registro non vuoto", bool(features.FEATURES), len(features.FEATURES))
    check(
        "ogni feature dichiara default bool, owner e descrizione",
        all(
            isinstance(feature.default, bool)
            and isinstance(feature.owner, str)
            and feature.owner.strip()
            and isinstance(feature.description, str)
            and feature.description.strip()
            for feature in features.FEATURES.values()
        ),
        features.describe(),
    )
    check(
        "nessuna feature accesa di default (l'output non cambia da solo)",
        all(feature.default is False for feature in features.FEATURES.values()),
        features.defaults(),
    )
    check(
        "names() == chiavi del registro", features.names() == sorted(features.FEATURES)
    )
    check(
        "describe() espone nome -> {default, owner, description}",
        features.describe()["stage_cache"]
        == {
            "default": features.FEATURES["stage_cache"].default,
            "owner": features.FEATURES["stage_cache"].owner,
            "description": features.FEATURES["stage_cache"].description,
        },
    )


def test_settings_table() -> None:
    _fresh()
    built = settings.build()
    check(
        "DEFAULTS['features'] e' il registro",
        built["features"] == features.defaults(),
        built["features"],
    )
    check(
        "default effettivo = default del registro",
        features.effective() == features.defaults(),
        features.effective(),
    )
    on = settings.build(overrides=[f"features.{features.names()[0]}=on"])
    check(
        "--override features.<name>=on arriva alla tabella",
        features.effective(on)[features.names()[0]] is True,
        on["features"],
    )
    settings.apply(on)
    check(
        "settings applicato: la feature e' on",
        features.enabled(features.names()[0]) is True,
    )
    check(
        "describe() riporta i flag effettivi",
        settings.describe()["features"] == features.effective(),
        settings.describe()["features"],
    )
    _fresh()


def test_unknown_name() -> None:
    _fresh()
    try:
        features.validate("non_esiste")
        check("nome ignoto: VxError", False, "nessuna eccezione")
    except contract.VxError as exc:
        message = str(exc)
        check("nome ignoto: VxError", True, message)
        check("il messaggio nomina il flag ignoto", "non_esiste" in message)
        check(
            "il messaggio elenca i flag validi",
            all(name in message for name in features.names()),
            features.names(),
        )
    try:
        features.enabled("non_esiste")
        check("enabled() su nome ignoto: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("enabled() su nome ignoto: VxError", True)
    try:
        features.apply_disables({"features": {}}, ["non_esiste"])
        check("apply_disables su nome ignoto: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("apply_disables su nome ignoto: VxError", True)
    try:
        features.validate_table({"features": {"non_esiste": True}})
        check("validate_table su nome ignoto: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("validate_table su nome ignoto: VxError", True)
    try:
        features.validate_table({"features": {features.names()[0]: "forse"}})
        check("validate_table su valore non booleano: VxError", False, "nessuna")
    except contract.VxError:
        check("validate_table su valore non booleano: VxError", True)


def test_guard_counter() -> None:
    _fresh()
    calls = {"stage_cache": 0}

    def _blocked() -> str:
        calls["stage_cache"] += 1
        return "eseguito"

    flag = "stage_cache"
    first = features.guard(flag, _blocked)
    check("flag off: il blocco non esegue", calls[flag] == 0 and first is None, calls)
    settings.apply(settings.build(overrides=[f"features.{flag}=on"]))
    second = features.guard(flag, _blocked)
    check(
        "flag on: il blocco esegue una volta",
        calls[flag] == 1 and second == "eseguito",
        calls,
    )
    settings.apply(settings.build(overrides=[f"features.{flag}=off"]))
    third = features.guard(flag, _blocked)
    check(
        "flag spento di nuovo: nessuna nuova esecuzione",
        calls[flag] == 1 and third is None,
    )
    _fresh()


def test_cli_flag() -> None:
    _fresh()
    args = _namespace()
    check("--no-feature assente: lista vuota", args.no_feature == [], args.no_feature)
    args = _namespace("--no-feature", "stage_cache", "--no-feature", "stage_cache")
    check("--no-feature ripetibile", args.no_feature == ["stage_cache", "stage_cache"])

    cli._apply_settings(_namespace("--no-feature", "stage_cache"))
    check(
        "--no-feature spegne il flag nel settings effettivo",
        settings.get("features.stage_cache") is False,
        settings.get("features.stage_cache"),
    )

    cli._apply_settings(_namespace("--override", "features.stage_cache=on"))
    check(
        "--override lo accende",
        settings.get("features.stage_cache") is True,
        settings.get("features.stage_cache"),
    )

    try:
        cli._apply_settings(_namespace("--no-feature", "non_esiste"))
        check("--no-feature ignoto: VxError (exit 2)", False, "nessuna eccezione")
    except contract.VxError:
        check("--no-feature ignoto: VxError (exit 2)", True)

    try:
        cli._apply_settings(_namespace("--override", "features.non_esiste=on"))
        check("--override su flag ignoto: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("--override su flag ignoto: VxError", True)

    run_args = cli.build_parser().parse_args(
        ["run", "--input", "in.mp4", "--no-feature", "stage_cache"]
    )
    check(
        "anche `run` accetta --no-feature",
        run_args.no_feature == ["stage_cache"],
        run_args.no_feature,
    )
    _fresh()


def test_report_builder() -> None:
    _fresh()
    flags = features.effective()
    check(
        "il report builder riceve il dizionario dei flag effettivi",
        set(flags) == set(features.FEATURES),
        flags,
    )
    source = Path(auto.__file__).read_text(encoding="utf-8")
    check("auto scrive 'features' nel report", '"features": flags,' in source)
    check("auto scrive 'events_path' nel report", '"events_path":' in source)
    check(
        "flags vengono da features.effective()",
        "flags = features_mod.effective()" in source,
    )
    settings.apply(settings.build(overrides=["features.stage_cache=on"]))
    check(
        "con il flag on il report vedrebbe True",
        features.effective()["stage_cache"] is True,
        features.effective(),
    )
    _fresh()


def main() -> int:
    test_registry()
    test_settings_table()
    test_unknown_name()
    test_guard_counter()
    test_cli_flag()
    test_report_builder()
    _fresh()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
