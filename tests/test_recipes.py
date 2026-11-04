"""Recipes are data outside the skill: validated, versioned, and unable to break the core."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, recipe, settings  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:62} {detail}")


def write_json(directory: Any, stem: str, data: dict) -> Path:
    path = Path(directory) / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def recipe_doc(stem: str, **overrides: object) -> dict:
    data = {
        "schema": recipe.SCHEMA,
        "name": stem,
        "description": f"ricetta {stem}",
        "extends": None,
        "brief": {},
        "settings": {},
        "params": {},
    }
    data.update(overrides)
    return data


@contextlib.contextmanager
def sandbox(included: Path, user: Path):
    """Point discovery at two temp roots; restore the module and env afterwards."""
    previous_included = recipe._included_dir
    previous_env = os.environ.get("VX_RECIPE_DIR")
    recipe._included_dir = lambda: Path(included)
    os.environ["VX_RECIPE_DIR"] = str(user)
    try:
        yield
    finally:
        recipe._included_dir = previous_included
        if previous_env is None:
            os.environ.pop("VX_RECIPE_DIR", None)
        else:
            os.environ["VX_RECIPE_DIR"] = previous_env


def error_of(fn, *args):
    try:
        fn(*args)
    except contract.VxError as exc:
        return str(exc)
    return ""


EXPECTED_BEAT = {
    "beat_grid": "on",
    "shot_max": 0.8,
    "effects_per_shot": 1.0,
    "font": "Antonio",
    "font_size": 106,
    "hook_at": 0.1,
    "cta": True,
    "loudness": -10.5,
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        empty_user = base / "empty_user"
        empty_user.mkdir()

        # -- the bundled first recipe ------------------------------------- #
        with sandbox(ROOT / "recipes", empty_user):
            check("SCHEMA e' vx.recipe/1", recipe.SCHEMA == "vx.recipe/1", recipe.SCHEMA)
            check(
                "paths() espone incluse e utente",
                recipe.paths()["included"] == ROOT / "recipes"
                and recipe.paths()["user"] == empty_user,
                recipe.paths(),
            )
            rows = recipe.list_all()
            beat_row = next((row for row in rows if row["name"] == "beat-cut"), None)
            check(
                "beat-cut scoperta come inclusa, non shadowed",
                beat_row is not None
                and beat_row["source"] == "included"
                and beat_row["shadowed"] is False
                and beat_row["valid"] is True,
                beat_row,
            )
            beat = recipe.load("beat-cut")
            check("beat-cut carica", beat["name"] == "beat-cut", beat["name"])
            check(
                "beat-cut ha i parametri del reel",
                beat["params"] == EXPECTED_BEAT,
                beat["params"],
            )
            check(
                "beat-cut alza render.effect_budget a 4",
                beat["settings"]["render"]["effect_budget"] == 4,
                beat["settings"],
            )
            raw_beat = json.loads((ROOT / "recipes" / "beat-cut.json").read_text("utf-8"))
            check("beat-cut passa validate senza problemi", recipe.validate(raw_beat) == [])
            check(
                "beat-cut ha una sola sorgente",
                len(beat["sources"]) == 1 and beat["sources"][0].endswith("beat-cut.json"),
                beat["sources"],
            )

        # -- discovery and user override ---------------------------------- #
        user = base / "user"
        write_json(user, "mine", recipe_doc("mine", params={"shot_max": 1.5}))
        write_json(
            user,
            "beat-cut",
            recipe_doc("beat-cut", description="override utente", params={"font": "Oswald"}),
        )
        with sandbox(ROOT / "recipes", user):
            rows = recipe.list_all()
            mine = next((row for row in rows if row["name"] == "mine"), None)
            check(
                "ricetta utente scoperta e non shadowed",
                mine is not None
                and mine["source"] == "user"
                and mine["shadowed"] is False,
                mine,
            )
            beats = [row for row in rows if row["name"] == "beat-cut"]
            over = beats[0] if beats else None
            check(
                "override utente visibile con shadowed=True",
                len(beats) == 1
                and over is not None
                and over["source"] == "user"
                and over["shadowed"] is True
                and over["shadowed_path"] is not None
                and Path(over["shadowed_path"]).name == "beat-cut.json",
                over,
            )
            loaded = recipe.load("beat-cut")
            check(
                "load sceglie l'override utente",
                loaded["description"] == "override utente"
                and loaded["params"]["font"] == "Oswald",
                loaded["description"],
            )

        # -- extends: two levels, deep merge, defaults -------------------- #
        inc2 = base / "inc2"
        user2 = base / "user2"
        write_json(
            inc2,
            "base",
            recipe_doc(
                "base",
                brief={"tone": "raw"},
                settings={"render": {"effect_budget": 2}},
                params={"shot_max": 1.5, "font_size": 72},
            ),
        )
        write_json(
            user2,
            "child",
            recipe_doc(
                "child",
                extends="base",
                brief={"platform": "tiktok"},
                settings={"render": {"effect_budget": 4}},
                params={"shot_max": 0.8},
            ),
        )
        with sandbox(inc2, user2):
            child = recipe.load("child")
            check(
                "extends a 2 livelli fa merge profondo di settings/brief",
                child["settings"]["render"]["effect_budget"] == 4
                and child["brief"] == {"tone": "raw", "platform": "tiktok"},
                child["brief"],
            )
            check(
                "extends eredita i parametri non ridefiniti",
                child["params"]["shot_max"] == 0.8
                and child["params"]["font_size"] == 72
                and child["params"]["font"] == "Antonio"
                and child["params"]["beat_grid"] == "off",
                child["params"],
            )
            check(
                "sources elenca base poi figlia",
                [Path(source).stem for source in child["sources"]] == ["base", "child"],
                child["sources"],
            )

        # -- cycles and depth --------------------------------------------- #
        cyc = base / "cyc"
        write_json(cyc, "a", recipe_doc("a", extends="b"))
        write_json(cyc, "b", recipe_doc("b", extends="a"))
        with sandbox(cyc, base / "empty_cyc"):
            (base / "empty_cyc").mkdir(exist_ok=True)
            cycle_error = error_of(recipe.load, "a")
            check(
                "ciclo di extends rifiutato",
                "cycle" in cycle_error.lower() and "a -> b -> a" in cycle_error,
                cycle_error,
            )

        deep = base / "deep"
        for number in range(1, 6):
            parent = f"u{number + 1}" if number < 5 else None
            write_json(deep, f"u{number}", recipe_doc(f"u{number}", extends=parent))
        with sandbox(deep, base / "empty_deep"):
            (base / "empty_deep").mkdir(exist_ok=True)
            depth_error = error_of(recipe.load, "u1")
            check(
                "profondita' > 4 rifiutata",
                "5 levels" in depth_error and "maximum is 4" in depth_error,
                depth_error,
            )
            depth4 = recipe.load("u2")
            check(
                "4 livelli sono ammessi",
                len(depth4["sources"]) == 4,
                len(depth4["sources"]),
            )

        # -- validation: schema, settings, params ------------------------- #
        bad_schema = recipe_doc("bad-schema")
        bad_schema["schema"] = "vx.recipe/2"
        schema_problems = recipe.validate(bad_schema)
        check(
            "schema sbagliato rifiutato",
            any("vx.recipe/1" in problem for problem in schema_problems),
            schema_problems,
        )
        bad_user = base / "bad_user"
        bad_user.mkdir()
        write_json(bad_user, "bad-schema", bad_schema)
        with sandbox(base / "none", bad_user):
            check(
                "load rifiuta lo schema sbagliato",
                "schema must be" in error_of(recipe.load, "bad-schema"),
                error_of(recipe.load, "bad-schema"),
            )

        typo = recipe_doc(
            "typo", settings={"render": {"effect_budgt": 4}}
        )
        typo_problems = recipe.validate(typo)
        check(
            "chiave settings inesistente rifiutata con suggerimento",
            any("effect_budget" in problem and "did you mean" in problem for problem in typo_problems),
            typo_problems,
        )

        unknown_param = recipe.validate(recipe_doc("p", params={"shot_mx": 0.8}))
        check(
            "parametro sconosciuto rifiutato con suggerimento",
            any("shot_max" in problem and "did you mean" in problem for problem in unknown_param),
            unknown_param,
        )
        wrong_type = recipe.validate(recipe_doc("p", params={"shot_max": "0.8"}))
        check(
            "parametro di tipo sbagliato rifiutato",
            any("must be a number" in problem for problem in wrong_type),
            wrong_type,
        )
        out_of_range = recipe.validate(recipe_doc("p", params={"loudness": -30}))
        check(
            "parametro fuori range rifiutato",
            any("outside [-23.0, -5.0]" in problem for problem in out_of_range),
            out_of_range,
        )
        float_size = recipe.validate(recipe_doc("p", params={"font_size": 106.5}))
        check(
            "font_size accetta solo interi",
            any("must be an integer" in problem for problem in float_size),
            float_size,
        )
        bad_choice = recipe.validate(recipe_doc("p", params={"beat_grid": "yes"}))
        check(
            "beat_grid accetta solo on/off",
            any("must be one of" in problem for problem in bad_choice),
            bad_choice,
        )

        # -- load_safe degrades instead of raising ------------------------ #
        broken_user = base / "broken_user"
        broken_user.mkdir()
        (broken_user / "broken.json").write_text("{ not json", encoding="utf-8")
        with sandbox(base / "none2", broken_user):
            safe_recipe, safe_problems = recipe.load_safe("broken")
            check(
                "load_safe su file rotto restituisce None e problemi",
                safe_recipe is None and bool(safe_problems),
                safe_problems,
            )
            missing_recipe, missing_problems = recipe.load_safe("does-not-exist")
            check(
                "load_safe su nome assente degrada",
                missing_recipe is None and bool(missing_problems),
                missing_problems,
            )
            good_recipe, good_problems = recipe.load_safe("broken")
            check(
                "load_safe non inventa una ricetta valida per un file rotto",
                good_recipe is None and bool(good_problems),
                good_recipe,
            )

        # -- write_layer is a real vx.settings layer ---------------------- #
        with sandbox(ROOT / "recipes", empty_user):
            beat = recipe.load("beat-cut")
            layer_path = recipe.write_layer(beat, base / "out" / "beat-layer.json")
            written = json.loads(layer_path.read_text(encoding="utf-8"))
            built = settings.build(settings_file=str(layer_path))
            check(
                "write_layer scrive il layer, non un wrapper",
                written == {"render": {"effect_budget": 4}},
                written,
            )
            check(
                "settings.build accetta il layer e cambia effect_budget",
                built["render"]["effect_budget"] == 4,
                built["render"]["effect_budget"],
            )
            bad_layer_error = error_of(
                recipe.write_layer,
                {"settings": {"render": {"effect_budgt": 4}}},
                base / "out" / "bad.json",
            )
            check(
                "write_layer rifiuta una chiave inesistente",
                "did you mean" in bad_layer_error,
                bad_layer_error,
            )

        # -- a bundled recipe may never extend a user recipe -------------- #
        inc_ext = base / "inc_ext"
        user_ext = base / "user_ext"
        write_json(inc_ext, "leaf", recipe_doc("leaf", extends="user-only"))
        write_json(user_ext, "user-only", recipe_doc("user-only"))
        with sandbox(inc_ext, user_ext):
            leaf, leaf_problems = recipe.load_safe("leaf")
            check(
                "una ricetta inclusa non puo' estendere una utente",
                leaf is None
                and any("included" in problem and "user" in problem for problem in leaf_problems),
                leaf_problems,
            )

        # -- a user override cannot change what a bundled recipe inherits - #
        inc_shadow = base / "inc_shadow"
        user_shadow = base / "user_shadow"
        write_json(
            inc_shadow,
            "base",
            recipe_doc("base", settings={"render": {"effect_budget": 2}}),
        )
        write_json(inc_shadow, "leaf", recipe_doc("leaf", extends="base"))
        write_json(
            user_shadow,
            "base",
            recipe_doc("base", settings={"render": {"effect_budget": 9}}),
        )
        with sandbox(inc_shadow, user_shadow):
            leaf = recipe.load("leaf")
            check(
                "l'override utente non cambia l'eredita' della ricetta inclusa",
                leaf["settings"]["render"]["effect_budget"] == 2,
                leaf["settings"],
            )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
