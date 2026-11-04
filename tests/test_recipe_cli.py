"""``vx recipe`` surfaces the recipe module: list, show, validate, init.

No network, no ffmpeg: ``VX_RECIPE_DIR`` points at a temp folder for the whole
run, so nothing touches ``~/.vx``. The bundled ``recipes/beat-cut.json`` stays
the real included layer.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, recipe  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:60} {detail}")


@contextlib.contextmanager
def user_recipes(path: Path):
    previous = os.environ.get("VX_RECIPE_DIR")
    os.environ["VX_RECIPE_DIR"] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop("VX_RECIPE_DIR", None)
        else:
            os.environ["VX_RECIPE_DIR"] = previous


def run_cli(argv: list) -> tuple:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def recipe_doc(name: str, **overrides: object) -> dict:
    data = {
        "schema": recipe.SCHEMA,
        "name": name,
        "description": f"ricetta {name}",
        "extends": None,
        "brief": {},
        "settings": {},
        "params": {},
    }
    data.update(overrides)
    return data


def write_json(directory: Path, name: str, data: dict) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-recipe-cli-") as raw:
        base = Path(raw)
        user = base / "user"
        user.mkdir()

        with user_recipes(user):
            # -- list: included recipe ------------------------------------- #
            code, out, err = run_cli(["recipe", "list"])
            check(
                "list: vede beat-cut come included",
                code == 0 and "beat-cut" in out and "included" in out,
                out.splitlines()[:2] or err,
            )

            # -- list: user recipe and shadowing --------------------------- #
            write_json(user, "mine", recipe_doc("mine", params={"shot_max": 1.5}))
            code, out, _ = run_cli(["recipe", "list"])
            mine_line = next((line for line in out.splitlines() if "mine" in line), "")
            check(
                "list: vede la ricetta utente con origine user",
                code == 0 and mine_line and "user" in mine_line,
                mine_line,
            )

            write_json(
                user,
                "beat-cut",
                recipe_doc("beat-cut", description="override utente", params={"font": "Oswald"}),
            )
            code, out, _ = run_cli(["recipe", "list"])
            beat_lines = [line for line in out.splitlines() if line.startswith("beat-cut")]
            check(
                "list: l'override utente e' shadowed e una riga sola",
                code == 0
                and len(beat_lines) == 1
                and "user" in beat_lines[0]
                and "shadowed" in beat_lines[0],
                beat_lines,
            )

            # -- show: params and which ones are defaults ------------------ #
            code, out, err = run_cli(["recipe", "show", "mine"])
            check(
                "show: stampa il param esplicito",
                code == 0 and "shot_max = 1.5" in out,
                err or out,
            )
            check(
                "show: stampa i default (font_size) e li evidenzia",
                code == 0 and "font_size = 64" in out and "(default)" in out and "Antonio" in out,
                out,
            )

            code, _out, err = run_cli(["recipe", "show", "does-not-exist"])
            check(
                "show: nome inesistente esce 2 con messaggio",
                code == 2 and "not found" in err.lower(),
                err,
            )

            # -- show: invalid recipe, all problems ------------------------ #
            write_json(
                user,
                "bad",
                recipe_doc("bad", schema="vx.recipe/2", extra_key=True),
            )
            code, _out, err = run_cli(["recipe", "show", "bad"])
            check(
                "show: ricetta invalida esce 2 con tutti i problemi",
                code == 2
                and "schema must be" in err
                and "unknown recipe key" in err
                and "Traceback" not in err,
                err,
            )
            (user / "bad.json").unlink()

            # -- validate: every discovered recipe ------------------------- #
            code, out, err = run_cli(["recipe", "validate"])
            check(
                "validate: tutte le ricette scoperte sono OK",
                code == 0 and out.count("OK") >= 2 and "FAIL" not in out,
                out or err,
            )

            # -- validate: a name ------------------------------------------ #
            code, out, err = run_cli(["recipe", "validate", "mine"])
            check(
                "validate: un nome valido dice OK",
                code == 0 and "OK" in out and "mine" in out,
                out or err,
            )

            # -- validate: a broken JSON file, not a traceback ------------- #
            broken = user / "broken.json"
            broken.write_text("{ not json", encoding="utf-8")
            code, out, err = run_cli(["recipe", "validate", str(broken)])
            check(
                "validate: file rotto esce 2 col problema, senza traceback",
                code == 2
                and "not valid JSON" in out
                and "Traceback" not in out
                and "Traceback" not in err,
                out or err,
            )
            code, out, _ = run_cli(["recipe", "list"])
            check(
                "list: una ricetta rotta e' INVALIDA col primo problema",
                code == 0
                and "INVALIDA" in out
                and "not valid JSON" in out,
                out,
            )
            broken.unlink()

            # -- init: creates a loadable user recipe ---------------------- #
            code, out, err = run_cli(["recipe", "init", "alt"])
            created = user / "alt.json"
            check(
                "init: crea il file nella cartella utente",
                code == 0 and created.is_file() and str(created) in out,
                err or out,
            )
            check(
                "init: recipe.load accetta la ricetta creata",
                created.is_file() and recipe.load("alt")["name"] == "alt",
                created,
            )
            data = json.loads(created.read_text(encoding="utf-8")) if created.is_file() else {}
            check(
                "init: solo schema/name/description/params, niente settings",
                set(data) == {"schema", "name", "description", "params"}
                and set(data.get("params") or {}) == {"shot_max", "loudness"},
                sorted(data),
            )

            before = created.read_text(encoding="utf-8")
            code, _out, err = run_cli(["recipe", "init", "alt"])
            check(
                "init: file esistente rifiutato senza --force",
                code == 2
                and "already exists" in err.lower()
                and created.read_text(encoding="utf-8") == before,
                err,
            )
            code, _out, err = run_cli(["recipe", "init", "alt", "--force"])
            check(
                "init: --force sovrascrive",
                code == 0 and created.is_file(),
                err,
            )

            # -- init --extends -------------------------------------------- #
            code, out, err = run_cli(
                ["recipe", "init", "ext", "--extends", "beat-cut"]
            )
            ext_path = user / "ext.json"
            ext = json.loads(ext_path.read_text(encoding="utf-8")) if ext_path.is_file() else {}
            base_params = recipe.load("beat-cut")["params"]
            check(
                "init --extends: file valido che eredita i valori della base",
                code == 0
                and ext.get("extends") == "beat-cut"
                and set(ext) == {"schema", "name", "description", "extends", "params"}
                and recipe.load("ext")["params"] == base_params,
                err or sorted(ext),
            )

            # -- --json documents ------------------------------------------ #
            code, out, err = run_cli(["recipe", "list", "--json"])
            try:
                rows = json.loads(out)
            except json.JSONDecodeError:
                rows = None
            shadow = next((row for row in rows or [] if row.get("name") == "beat-cut"), None)
            check(
                "list --json: JSON parsabile con beat-cut shadowed",
                code == 0 and isinstance(rows, list) and bool(shadow)
                and shadow.get("source") == "user" and shadow.get("shadowed") is True,
                err or out[-200:],
            )

            code, out, err = run_cli(["recipe", "show", "mine", "--json"])
            try:
                shown = json.loads(out)
            except json.JSONDecodeError:
                shown = None
            check(
                "show --json: params e defaults nel documento",
                code == 0
                and isinstance(shown, dict)
                and shown.get("params", {}).get("font_size") == 64
                and "font_size" in (shown.get("defaults") or []),
                err or out[-200:],
            )

        # -- parser contract for the other agent --------------------------- #
        parser = cli.build_parser()
        with_recipe = parser.parse_args(
            [
                "auto",
                "--prompt",
                "reel",
                "--assets",
                "assets",
                "--out",
                "out.mp4",
                "--recipe",
                "beat-cut",
            ]
        )
        without = parser.parse_args(
            ["auto", "--prompt", "reel", "--assets", "assets", "--out", "out.mp4"]
        )
        check(
            "build_parser: auto --recipe popola args.recipe",
            with_recipe.recipe == "beat-cut",
            with_recipe.recipe,
        )
        check(
            "build_parser: auto senza --recipe ha args.recipe None",
            without.recipe is None,
            without.recipe,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
