"""vx.recipe — declarative compositions that live outside the skill.

A recipe is data, not code: one JSON file declares settings, a brief fragment and
a handful of named parameters.  The module validates every layer before it is
merged, so a malformed recipe is a named error instead of a silent change to the
core.  Recipes ship with the skill (``<skill>/recipes/*.json``) and the user can
override them from ``~/.vx/recipes`` (or ``$VX_RECIPE_DIR`` in tests).  A user
recipe may extend a bundled one; a bundled recipe may never extend a user file.

The first recipe is ``beat-cut``: tight cuts on the rhythmic grid, impact text,
dark grade and -10.5 LUFS.  The name says what it does, not what inspired it.
"""

from __future__ import annotations

import difflib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import contract, settings

SCHEMA = "vx.recipe/1"
MAX_DEPTH = 4

#: Every parameter a recipe may set.  ``type`` is one of bool/int/number/string/
#: choice; ``range`` is inclusive; ``default`` is what a recipe that omits the
#: key inherits.  An unknown name is an error, never a silently inert value.
PARAMS: Dict[str, Dict[str, Any]] = {
    "beat_grid": {
        "type": "choice",
        "choices": ("on", "off"),
        "default": "off",
        "description": "taglia sulla griglia ritmica invece che sui confini semantici",
    },
    "shot_max": {
        "type": "number",
        "default": 2.0,
        "range": (0.1, 10.0),
        "description": "durata massima di una inquadratura, in secondi",
    },
    "effects_per_shot": {
        "type": "number",
        "default": 1.0,
        "range": (0.0, 4.0),
        "description": "numero medio di effetti di libreria per inquadratura",
    },
    "font": {
        "type": "string",
        "default": "Antonio",
        "description": "famiglia del testo d'impatto",
    },
    "font_size": {
        "type": "int",
        "default": 64,
        "range": (24, 400),
        "description": "corpo del testo d'impatto, in pixel sul canvas di uscita",
    },
    "hook_at": {
        "type": "number",
        "default": 0.0,
        "range": (0.0, 10.0),
        "description": "istante del testo d'apertura, in secondi",
    },
    "cta": {
        "type": "bool",
        "default": False,
        "description": "chiude con una chiamata all'azione",
    },
    "loudness": {
        "type": "number",
        "default": -14.0,
        "range": (-23.0, -5.0),
        "description": "loudness integrata di consegna, in LUFS",
    },
}

_TOP_KEYS = frozenset(
    {"schema", "name", "description", "extends", "brief", "settings", "params"}
)


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _included_dir() -> Path:
    return _skill_root() / "recipes"


def _user_dir() -> Path:
    override = os.environ.get("VX_RECIPE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".vx" / "recipes"


def paths() -> Dict[str, Path]:
    """The two search roots: bundled (base layer) and user (override layer)."""
    return {"included": _included_dir(), "user": _user_dir()}


def _scan(directory: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        if path.is_file():
            found[path.stem] = path
    return found


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise contract.VxError(f"cannot read recipe {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise contract.VxError(f"recipe {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise contract.VxError(
            f"recipe {path} must contain a JSON object, got {type(data).__name__}"
        )
    return data


def _suggest(word: str, choices: Any) -> Optional[str]:
    matches = difflib.get_close_matches(str(word), [str(c) for c in choices], n=1, cutoff=0.5)
    return matches[0] if matches else None


def _settings_tree() -> Tuple[Dict[str, List[str]], set]:
    """Valid settings branches and leaves, derived from ``settings.DEFAULTS``.

    ``thresholds`` is an empty dict in DEFAULTS because the real names live in
    ``contract.DEFAULT_THRESHOLDS``; the declared threshold keys are valid layer
    keys too, otherwise a recipe could not tune what the cascade already tunes.
    """
    children: Dict[str, List[str]] = {}
    leaves: set = set()

    def walk(node: Dict[str, Any], path: str) -> None:
        children[path] = sorted(str(key) for key in node)
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, dict):
                walk(value, child)
            else:
                leaves.add(child)

    walk(settings.DEFAULTS, "")
    children["thresholds"] = sorted(contract.DEFAULT_THRESHOLDS)
    for key in contract.DEFAULT_THRESHOLDS:
        leaves.add(f"thresholds.{key}")
    return children, leaves


def _settings_problems(layer: Any) -> List[str]:
    if not isinstance(layer, dict):
        return [f"settings must be an object, got {type(layer).__name__}"]
    children, leaves = _settings_tree()
    problems: List[str] = []

    def walk(node: Dict[str, Any], path: str) -> None:
        allowed = children.get(path, [])
        for key, value in node.items():
            full = f"{path}.{key}" if path else str(key)
            shown = f"settings.{full}"
            if full in children:
                if not isinstance(value, dict):
                    problems.append(f"{shown} must be an object")
                else:
                    walk(value, full)
            elif full in leaves:
                if isinstance(value, dict):
                    problems.append(f"{shown} must be a value, not an object")
            else:
                suggestion = _suggest(key, allowed)
                if suggestion:
                    hint = (
                        f"settings.{path}.{suggestion}" if path else f"settings.{suggestion}"
                    )
                    problems.append(
                        f"unknown settings key '{shown}'; did you mean '{hint}'?"
                    )
                else:
                    valid = ", ".join(allowed) if allowed else "(none)"
                    problems.append(
                        f"unknown settings key '{shown}'; valid keys under "
                        f"settings.{path or '<root>'}: {valid}"
                    )

    walk(layer, "")
    return problems


def _params_problems(params: Any) -> List[str]:
    if not isinstance(params, dict):
        return [f"params must be an object, got {type(params).__name__}"]
    problems: List[str] = []
    known = sorted(PARAMS)
    for name, value in params.items():
        spec = PARAMS.get(name)
        if spec is None:
            suggestion = _suggest(name, known)
            hint = f"; did you mean '{suggestion}'?" if suggestion else ""
            problems.append(
                f"unknown parameter 'params.{name}'{hint} known parameters: "
                + ", ".join(known)
            )
            continue
        kind = spec["type"]
        shown = f"params.{name}"
        if kind == "bool":
            if not isinstance(value, bool):
                problems.append(f"{shown} must be a bool, got {value!r}")
        elif kind == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                problems.append(f"{shown} must be an integer, got {value!r}")
            elif "range" in spec:
                low, high = spec["range"]
                if not (low <= value <= high):
                    problems.append(f"{shown}={value} outside [{low}, {high}]")
        elif kind == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(f"{shown} must be a number, got {value!r}")
            elif "range" in spec:
                low, high = spec["range"]
                if not (low <= float(value) <= high):
                    problems.append(f"{shown}={value} outside [{low}, {high}]")
        elif kind == "string":
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{shown} must be a non-empty string, got {value!r}")
        elif kind == "choice":
            choices = spec["choices"]
            if value not in choices:
                problems.append(f"{shown} must be one of {list(choices)}, got {value!r}")
        else:  # pragma: no cover - a typo in PARAMS itself, not in a recipe
            problems.append(f"{shown} declares unknown type {kind!r}")
    return problems


def validate(data: Any) -> List[str]:
    """Return every problem in one recipe document; never raise on bad data."""
    if not isinstance(data, dict):
        return [f"recipe must be a JSON object, got {type(data).__name__}"]
    problems: List[str] = []

    for key in data:
        if key not in _TOP_KEYS:
            suggestion = _suggest(key, _TOP_KEYS)
            hint = f"; did you mean '{suggestion}'?" if suggestion else ""
            problems.append(f"unknown recipe key '{key}'{hint}")

    schema = data.get("schema")
    if schema != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}, got {schema!r}")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name must be a non-empty string")
    elif name in (".", "..") or "/" in name or "\\" in name:
        problems.append(f"name {name!r} must be a file stem, not a path")

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append("description must be a non-empty string")
    elif "\n" in description or "\r" in description:
        problems.append("description must be a single line")

    extends = data.get("extends")
    if extends is not None:
        if not isinstance(extends, str) or not extends.strip():
            problems.append("extends must be null or a non-empty recipe name")
        elif extends == name:
            problems.append(f"extends cannot point to the recipe itself ({name!r})")

    brief = data.get("brief")
    if brief is not None and not isinstance(brief, dict):
        problems.append(f"brief must be an object, got {type(brief).__name__}")

    if "settings" in data:
        problems.extend(_settings_problems(data.get("settings")))
    if "params" in data:
        problems.extend(_params_problems(data.get("params")))
    return problems


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _lookup(name: str, scope: str) -> Tuple[Path, str]:
    """Resolve one name inside a scope.

    ``scope='included'`` means a bundled recipe is asking: only bundled files are
    visible, so a user override can never change what a bundled recipe inherits.
    ``scope='user'`` is the normal precedence, user first, bundled as fallback.
    """
    included = _scan(_included_dir())
    user = _scan(_user_dir())
    if scope == "included":
        if name in included:
            return included[name], "included"
        if name in user:
            raise contract.VxError(
                f"included recipe '{name}' cannot extend user recipe '{name}': "
                "the skill must stay independent of the user's recipes"
            )
        raise contract.VxError(
            f"recipe '{name}' not found in {_included_dir()}"
        )
    if name in user:
        return user[name], "user"
    if name in included:
        return included[name], "included"
    raise contract.VxError(
        f"recipe '{name}' not found in {_user_dir()} or {_included_dir()}"
    )


def _chain(name: str, scope: str, stack: List[str]) -> List[Tuple[str, Path, str, Dict[str, Any]]]:
    if name in stack:
        cycle = " -> ".join(stack + [name])
        raise contract.VxError(f"extends cycle: {cycle}")

    path, origin = _lookup(name, scope)
    data = _read_json(path)
    problems = validate(data)
    declared = data.get("name")
    if declared != name:
        problems.append(
            f"file {path.name} declares name {declared!r}, expected {name!r}"
        )
    if problems:
        raise contract.VxError(
            f"invalid recipe '{name}': " + "; ".join(problems)
        )

    extends = data.get("extends")
    if extends:
        parent_scope = "included" if origin == "included" else "user"
        chain = _chain(extends, parent_scope, stack + [name])
        chain = chain + [(name, path, origin, data)]
    else:
        chain = [(name, path, origin, data)]

    if len(chain) > MAX_DEPTH:
        raise contract.VxError(
            f"recipe '{name}' resolves through {len(chain)} levels; "
            f"the maximum is {MAX_DEPTH}"
        )
    return chain


def _merge(chain: List[Tuple[str, Path, str, Dict[str, Any]]]) -> Dict[str, Any]:
    settings_layer: Dict[str, Any] = {}
    brief: Dict[str, Any] = {}
    params: Dict[str, Any] = {
        name: deepcopy(spec["default"]) for name, spec in PARAMS.items()
    }
    description = ""
    sources: List[str] = []
    extends = None

    for _name, path, _origin, data in chain:
        sources.append(str(path))
        description = data.get("description") or description
        settings_layer = _deep_merge(settings_layer, data.get("settings") or {})
        brief = _deep_merge(brief, data.get("brief") or {})
        params.update(deepcopy(data.get("params") or {}))
        extends = data.get("extends")

    leaf_name = chain[-1][0]
    return {
        "schema": SCHEMA,
        "name": leaf_name,
        "description": description,
        "extends": extends,
        "sources": sources,
        "settings": settings_layer,
        "brief": brief,
        "params": params,
    }


def load(name: str) -> Dict[str, Any]:
    """Resolve a recipe and its ``extends`` chain; raise ``VxError`` on any defect."""
    if not isinstance(name, str) or not name.strip():
        raise contract.VxError("recipe name must be a non-empty string")
    name = name.strip()
    return _merge(_chain(name, "user", []))


def load_safe(name: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Load for degradation: ``(recipe, [])`` or ``(None, problems)``."""
    try:
        return load(name), []
    except contract.VxError as exc:
        return None, [str(exc)]
    except (OSError, ValueError) as exc:
        return None, [f"{type(exc).__name__}: {exc}"]


def list_all() -> List[Dict[str, Any]]:
    """Every effective recipe, bundled first then user, sorted by name.

    A user file with the same name as a bundled one wins; the returned row says
    ``source='user'``, ``shadowed=True`` and names the bundled file it hides, so
    the override is visible instead of silent.
    """
    included = _scan(_included_dir())
    user = _scan(_user_dir())
    rows: List[Dict[str, Any]] = []

    for name in sorted(set(included) | set(user)):
        if name in user:
            path = user[name]
            source = "user"
            shadowed = name in included
            shadowed_path = included.get(name)
        else:
            path = included[name]
            source = "included"
            shadowed = False
            shadowed_path = None

        problems: List[str] = []
        description = ""
        try:
            data = _read_json(path)
            description = data.get("description") or ""
            problems = validate(data)
        except contract.VxError as exc:
            problems = [str(exc)]

        rows.append(
            {
                "name": name,
                "description": description,
                "path": str(path),
                "source": source,
                "shadowed": shadowed,
                "shadowed_path": str(shadowed_path) if shadowed_path else None,
                "valid": not problems,
                "problems": problems,
            }
        )
    return rows


def write_layer(recipe: Any, path: Any) -> Path:
    """Write the recipe's settings layer where ``settings.build(settings_file=...)`` reads it.

    The file is the layer itself (``{"render": {...}}``), not a wrapper: that is
    the shape ``vx.settings._file_layers`` merges into the effective tree.
    """
    if isinstance(recipe, str):
        recipe = load(recipe)
    if not isinstance(recipe, dict):
        raise contract.VxError(
            f"write_layer expects a recipe object or name, got {type(recipe).__name__}"
        )
    layer = recipe["settings"] if "settings" in recipe else recipe
    problems = _settings_problems(layer)
    if problems:
        raise contract.VxError("invalid settings layer: " + "; ".join(problems))

    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(layer, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return destination
