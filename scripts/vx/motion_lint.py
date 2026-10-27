"""vx.motion_lint — the static compiler for Motion Studio templates.

A template is a self-contained directory: `index.html`, `template.json`, and
whatever local assets the page names. Capture renders it in a browser whose only
permitted schemes are `file:`, `data:`, `about:` and `blob:`; everything else is
aborted at runtime. That block is a safety net, not a diagnosis: a remote
`<link>` becomes a missing font, a remote `<script>` becomes a page that never
defines `__vxSeek`, and both look like a frozen render. This module decides,
before any browser exists, whether a template can be captured at all.

The contract: `lint(template_dir)` never launches a browser and never touches
the network. It returns

    {
      "ok": bool,
      "findings": [{"code": str, "where": "file:line", "detail": str}, ...],
      "stats": {"files": int, "bytes": int, "deps": int},
    }

`findings` is ordered by (file, line, code, detail), so two runs over the same
bytes produce byte-identical JSON. `where` carries `file:line` for text
findings; template-level findings (a missing `__vxSeek`, a missing entry point)
name the file alone. `stats.files` counts the text files actually read,
`stats.bytes` their total size, and `stats.deps` the local dependency references
the template names.

Codes
-----
NETWORK_TAG     a `<script src>`, `<link href>`, `<img src>`, `<iframe src>`,
                `<video src|poster>`, `<audio src>` or `<source src>` pointing at
                `http://`, `https://` or a protocol-relative `//host`.
NETWORK_CSS     `url(http...)` or `@import` of a remote sheet, in a `<style>`
                block, a `style="..."` attribute, or a local `.css` file.
NETWORK_JS      `fetch(`, `XMLHttpRequest`, `importScripts(`, `WebSocket(` or a
                dynamic `import("remote")`.
CLOCK_MISSING   no assignment to `window.__vxSeek` was found, so the page is not
                a pure function of virtual time. When `runtime_check` is on and
                Playwright plus a system browser are available, the page is also
                loaded and `typeof window.__vxSeek` is confirmed to be
                `"function"`; the static check is mandatory, the runtime check
                is confirmation only.
DANGEROUS_JS    `eval(`, `new Function(` or `document.write(`.
DEP_MISSING     a local dependency (font, image, sprite, `.js`, `.css`,
                `@font-face src`) named by the template does not exist on disk.
DEP_ABSOLUTE    a dependency with a drive letter, a leading `/` or `\\`, or a
                `..` that escapes the template directory.
MANIFEST_INVALID `template.json` missing, not JSON, unknown top-level key, a
                parameter without `type`, a default outside its own domain,
                `fps` outside 1..120, a `size` component outside 16..8192, or
                `duration` <= 0.
NO_ENTRY        `index.html` is absent.

Parsing is honest, not a regex over the whole file. HTML goes through
`html.parser` (tags, attributes, inline `<script>`/`<style>` bodies); CSS is
scanned per block with comments blanked so a commented-out URL is not a finding;
JavaScript is a conservative token scan. The JavaScript heuristic, and its
limits, matter:

  * comments (`//`, `/* */`) are skipped, so the word `fetch` in a comment is
    not a call;
  * string and template literals become opaque tokens, so `fetch` inside a
    string — including a string used as a selector such as
    `querySelector('a[href="http"]')` — is not a call;
  * a match is an identifier token followed by `(` in the token stream, which
    is enough to avoid selectors and prose but is not an AST.

What the scanner cannot see, and a human must still check:

  * regular-expression literals are not recognised: a `/` inside a regex may be
    read as the start of a comment, and a quote inside a regex may desynchronise
    the string scanner;
  * computed dispatch (`window["fe" + "tch"](...)`, aliased `eval`) and dynamic
    `import(variable)` are invisible;
  * a locally shadowed function named `fetch` is still reported as network;
  * `Object.defineProperty(window, "__vxSeek", ...)` is not recognised as an
    assignment by the static pass (the runtime pass confirms it);
  * a `<a href>`, `<form action>` or `<object data>` is not a load-time fetch
    and is not flagged;
  * CSS `image-set()`, `src: local()`, `@font-face` names resolved by the OS,
    and remote URLs assembled at runtime are not seen;
  * the manifest is checked against its declared schema, not against what the
    template actually reads at runtime.

Standard library only. No network is touched. The runtime confirmation follows
Playwright availability when `runtime_check=None`, can be forced on with
`runtime_check=True`, and is deliberately disabled by `motion.capture`
(`runtime_check=False`) so a template that fails lint never causes a browser to
start.
"""

from __future__ import annotations

import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .contract import VxError

__all__ = [
    "CODES",
    "MotionLintError",
    "lint",
    "lint_or_raise",
    "runtime_probe",
]

CODES = (
    "NETWORK_TAG",
    "NETWORK_CSS",
    "NETWORK_JS",
    "CLOCK_MISSING",
    "DANGEROUS_JS",
    "DEP_MISSING",
    "DEP_ABSOLUTE",
    "MANIFEST_INVALID",
    "NO_ENTRY",
)

_ALLOWED_KEYS = frozenset({"name", "fps", "duration", "size", "params"})
_PARAM_TYPES = frozenset({"string", "number", "int", "color", "bool"})
_SAFE_SCHEMES = (
    "data:",
    "blob:",
    "about:",
    "javascript:",
    "mailto:",
    "tel:",
)
_TEXT_SUFFIXES = (".css", ".js")
_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_TAG_ATTRS = ("src", "poster")
_URL_RE = re.compile(
    r"""url\(\s*(?:'([^']*)'|"([^"]*)"|([^'")]*?))\s*\)""",
    re.IGNORECASE | re.DOTALL,
)
_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*(?:'([^']*)'|"([^"]*)"|([^'")]*?))\s*\)"""
    r"""|'([^']*)'|"([^"]*)")""",
    re.IGNORECASE | re.DOTALL,
)


class MotionLintError(VxError):
    """A template failed the static linter. Every finding travels with it."""

    code = "MOTION_LINT_FAILED"

    def __init__(self, template: str | Path, findings: Iterable[Dict[str, Any]]):
        self.template = str(template)
        self.findings: List[Dict[str, Any]] = [dict(item) for item in findings]
        self.payload: Dict[str, Any] = {
            "code": self.code,
            "template": self.template,
            "findings": self.findings,
        }
        named = ", ".join(sorted({item["code"] for item in self.findings}))
        super().__init__(
            f"{self.code}: {self.template}: {len(self.findings)} finding(s)"
            + (f": {named}" if named else "")
        )


def _templates_root() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "templates"


def _resolve_root(template_dir: str | Path) -> Path:
    """Accept a directory or a name under `assets/templates/`; never raise."""
    given = Path(template_dir)
    if given.is_dir():
        return given.resolve()
    named = _templates_root() / str(template_dir)
    if named.is_dir():
        return named.resolve()
    return given


def _finding(code: str, file: str, line: int, detail: str) -> Dict[str, Any]:
    return {"code": code, "file": file, "line": line, "detail": detail}


def _is_remote(value: str) -> bool:
    low = value.strip().lower()
    return (
        low.startswith("http://")
        or low.startswith("https://")
        or value.strip().startswith("//")
    )


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _key_line(text: str, key: str) -> int:
    match = re.search(r'"' + re.escape(key) + r'"\s*:', text)
    return _line_of(text, match.start()) if match else 0


def _blank_css_comments(css: str) -> str:
    """Replace comment bodies with spaces, keeping newlines for line numbers."""
    out = list(css)
    index = 0
    length = len(css)
    in_comment = False
    while index < length:
        if not in_comment and css.startswith("/*", index):
            in_comment = True
            out[index] = out[index + 1] = " "
            index += 2
        elif in_comment:
            if css.startswith("*/", index):
                in_comment = False
                out[index] = out[index + 1] = " "
                index += 2
            else:
                if css[index] != "\n":
                    out[index] = " "
                index += 1
        else:
            index += 1
    return "".join(out)


def _first_group(match: "re.Match[str]") -> Optional[str]:
    for group in match.groups():
        if group is not None:
            return group
    return None


class _HtmlScan(HTMLParser):
    """Collect tags, inline script/style bodies and dependency references."""

    def __init__(self, linter: "_Linter"):
        super().__init__(convert_charrefs=True)
        self.linter = linter
        self.script: Optional[List[Any]] = None
        self.style: Optional[List[Any]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        self._open(tag, attrs, self_closing=True)

    def _open(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
        *,
        self_closing: bool,
    ) -> None:
        tag = tag.lower()
        values = {name.lower(): (value or "") for name, value in attrs}
        line = self.getpos()[0]
        if tag == "script":
            source = values.get("src", "").strip()
            if source:
                self.linter.reference(
                    source, "index.html", line, self.linter.root, "script"
                )
            elif not self_closing:
                self.script = [line, []]
        elif tag == "style":
            if not self_closing:
                self.style = [line, []]
        elif tag == "link":
            href = values.get("href", "").strip()
            if href:
                self.linter.reference(
                    href, "index.html", line, self.linter.root, "link"
                )
        else:
            for attribute in _TAG_ATTRS:
                target = values.get(attribute, "").strip()
                if target:
                    self.linter.reference(
                        target, "index.html", line, self.linter.root, tag
                    )
        inline = values.get("style", "")
        if inline:
            self.linter.add_css(inline, "index.html", line, self.linter.root)

    def handle_data(self, data: str) -> None:
        line = self.getpos()[0]
        if self.script is not None:
            self.script[1].append((data, line))
        elif self.style is not None:
            self.style[1].append((data, line))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self.script is not None:
            for data, line in self.script[1]:
                self.linter.add_js(data, "index.html", line)
            self.script = None
        elif tag == "style" and self.style is not None:
            for data, line in self.style[1]:
                self.linter.add_css(data, "index.html", line, self.linter.root)
            self.style = None


class _Linter:
    def __init__(self, root: Path):
        self.root = root
        self.findings: List[Dict[str, Any]] = []
        self.sizes: Dict[str, int] = {}
        self.deps = 0
        self.has_seek = False
        self._js_seen: set = set()
        self._css_seen: set = set()

    # -- bookkeeping -----------------------------------------------------

    def add(self, code: str, file: str, line: int, detail: str) -> None:
        self.findings.append(_finding(code, file, line, detail))

    def read_text(self, path: Path, label: str) -> Optional[str]:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        self.sizes.setdefault(label, len(data))
        return data.decode("utf-8", errors="replace")

    def label_for(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    # -- dependencies ----------------------------------------------------

    def classify(self, url: str, base: Path) -> Tuple[str, Optional[Path]]:
        value = url.strip()
        if not value or value.startswith("#"):
            return "skip", None
        low = value.lower()
        if low.startswith(_SAFE_SCHEMES):
            return "skip", None
        if _is_remote(value):
            return "remote", None
        if _DRIVE.match(value) or value.startswith("/") or value.startswith("\\"):
            return "absolute", None
        relative = value.split("?", 1)[0].split("#", 1)[0]
        if not relative:
            return "skip", None
        try:
            target = (base / relative).resolve()
        except (OSError, ValueError):
            return "missing", None
        try:
            target.relative_to(self.root)
        except ValueError:
            return "escaped", None
        return ("exists" if target.exists() else "missing"), target

    def reference(self, url: str, label: str, line: int, base: Path, tag: str) -> None:
        kind, target = self.classify(url, base)
        if kind == "remote":
            code = "NETWORK_CSS" if tag == "css" else "NETWORK_TAG"
            self.add(code, label, line, f"{tag} fetches {url.strip()}")
            return
        if kind == "skip":
            return
        self.deps += 1
        if kind == "absolute":
            self.add(
                "DEP_ABSOLUTE",
                label,
                line,
                f"{tag} names an absolute path {url.strip()}",
            )
            return
        if kind == "escaped":
            self.add(
                "DEP_ABSOLUTE",
                label,
                line,
                f"{tag} escapes the template directory: {url.strip()}",
            )
            return
        if kind == "missing":
            self.add(
                "DEP_MISSING", label, line, f"{tag} needs a missing file {url.strip()}"
            )
            return
        if target is None:
            return
        suffix = target.suffix.lower()
        if suffix == ".js":
            self._schedule_js(target)
        elif suffix == ".css":
            self._schedule_css(target)

    def _schedule_js(self, path: Path) -> None:
        key = str(path)
        if key in self._js_seen:
            return
        self._js_seen.add(key)
        label = self.label_for(path)
        text = self.read_text(path, label)
        if text is not None:
            self.add_js(text, label, 1)

    def _schedule_css(self, path: Path) -> None:
        key = str(path)
        if key in self._css_seen:
            return
        self._css_seen.add(key)
        label = self.label_for(path)
        text = self.read_text(path, label)
        if text is not None:
            self.add_css(text, label, 1, path.parent)

    # -- CSS -------------------------------------------------------------

    def add_css(self, css: str, label: str, base_line: int, base: Path) -> None:
        cleaned = _blank_css_comments(css)
        taken: List[Tuple[int, int]] = []
        for match in _IMPORT_RE.finditer(cleaned):
            taken.append((match.start(), match.end()))
            url = _first_group(match)
            if url is not None:
                self.reference(
                    url,
                    label,
                    base_line + _line_of(cleaned, match.start()) - 1,
                    base,
                    "css",
                )
        for match in _URL_RE.finditer(cleaned):
            if any(start <= match.start() < end for start, end in taken):
                continue
            url = _first_group(match)
            if url is not None:
                self.reference(
                    url,
                    label,
                    base_line + _line_of(cleaned, match.start()) - 1,
                    base,
                    "css",
                )

    # -- JavaScript ------------------------------------------------------

    def add_js(self, code: str, label: str, base_line: int) -> None:
        tokens = _js_tokens(code)
        hits, seek = _scan_js_tokens(tokens)
        self.has_seek = self.has_seek or seek
        for offset, code_name, detail in hits:
            self.add(
                code_name,
                label,
                base_line + _line_of(code, offset) - 1,
                detail,
            )

    # -- manifest --------------------------------------------------------

    def lint_manifest(self) -> None:
        path = self.root / "template.json"
        if not path.is_file():
            self.add("MANIFEST_INVALID", "template.json", 0, "template.json is missing")
            return
        text = self.read_text(path, "template.json")
        if text is None:
            self.add(
                "MANIFEST_INVALID", "template.json", 0, "template.json is unreadable"
            )
            return
        try:
            meta = json.loads(text)
        except json.JSONDecodeError as exc:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                exc.lineno,
                f"not valid JSON: {exc.msg}",
            )
            return
        if not isinstance(meta, dict):
            self.add("MANIFEST_INVALID", "template.json", 1, "must be a JSON object")
            return
        for key in meta:
            if key not in _ALLOWED_KEYS:
                self.add(
                    "MANIFEST_INVALID",
                    "template.json",
                    _key_line(text, str(key)),
                    f"unknown top-level key {key!r}",
                )
        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "name"),
                "'name' must be a non-empty string",
            )
        fps = meta.get("fps")
        if isinstance(fps, bool) or not isinstance(fps, (int, float)):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "fps"),
                f"'fps' must be a number, got {fps!r}",
            )
        elif not 1 <= float(fps) <= 120:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "fps"),
                f"'fps' {fps!r} is outside 1..120",
            )
        duration = meta.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "duration"),
                f"'duration' must be a number, got {duration!r}",
            )
        elif float(duration) <= 0:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "duration"),
                f"'duration' {duration!r} must be > 0",
            )
        size = meta.get("size")
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "size"),
                f"'size' must be [width, height], got {size!r}",
            )
        else:
            for value in size:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 16 <= value <= 8192
                ):
                    self.add(
                        "MANIFEST_INVALID",
                        "template.json",
                        _key_line(text, "size"),
                        f"'size' component {value!r} is outside 16..8192",
                    )
        params = meta.get("params")
        if not isinstance(params, dict):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                _key_line(text, "params"),
                "'params' must be an object of declarations",
            )
            return
        for param_name, declaration in params.items():
            self._lint_param(text, param_name, declaration)

    def _lint_param(self, text: str, name: str, declaration: Any) -> None:
        line = _key_line(text, str(name))
        if not isinstance(declaration, dict):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} must declare an object",
            )
            return
        if "type" not in declaration:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} has no 'type'",
            )
            return
        kind = declaration.get("type")
        if kind not in _PARAM_TYPES:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} declares unknown type {kind!r}",
            )
            return
        if "default" not in declaration:
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} has no 'default'",
            )
            return
        default = declaration.get("default")
        values = declaration.get("values")
        if isinstance(values, (list, tuple)) and values:
            if default not in values:
                self.add(
                    "MANIFEST_INVALID",
                    "template.json",
                    line,
                    f"parameter {name!r} default {default!r} is not in {list(values)!r}",
                )
            return
        if kind in ("number", "int"):
            numeric = isinstance(default, (int, float)) and not isinstance(
                default, bool
            )
            integral = kind != "int" or (numeric and float(default) == int(default))
            if not numeric or not integral:
                self.add(
                    "MANIFEST_INVALID",
                    "template.json",
                    line,
                    f"parameter {name!r} default {default!r} is not a valid {kind}",
                )
                return
            low, high = declaration.get("min"), declaration.get("max")
            if (low is not None and default < low) or (
                high is not None and default > high
            ):
                self.add(
                    "MANIFEST_INVALID",
                    "template.json",
                    line,
                    f"parameter {name!r} default {default!r} is outside its min/max",
                )
            return
        if kind == "bool":
            if not isinstance(default, bool):
                self.add(
                    "MANIFEST_INVALID",
                    "template.json",
                    line,
                    f"parameter {name!r} default {default!r} is not a boolean",
                )
            return
        if not isinstance(default, str):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} default {default!r} is not a string",
            )
            return
        if kind == "color" and not _HEX_COLOR.fullmatch(default):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} default {default!r} is not a hex colour",
            )
        limit = declaration.get("max_length")
        if (
            isinstance(limit, int)
            and not isinstance(limit, bool)
            and len(default) > limit
        ):
            self.add(
                "MANIFEST_INVALID",
                "template.json",
                line,
                f"parameter {name!r} default is {len(default)} chars, ceiling {limit}",
            )


def _js_tokens(code: str) -> List[Tuple[str, str, int]]:
    """Conservative token scan: comments skipped, literals kept as one token."""
    tokens: List[Tuple[str, str, int]] = []
    index = 0
    length = len(code)
    while index < length:
        char = code[index]
        if char in " \t\r\n\f\v":
            index += 1
            continue
        if code.startswith("//", index):
            newline = code.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if code.startswith("/*", index):
            closing = code.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if char in "'\"`":
            quote = char
            cursor = index + 1
            buffer: List[str] = []
            while cursor < length:
                current = code[cursor]
                if current == "\\" and cursor + 1 < length:
                    buffer.append(code[cursor : cursor + 2])
                    cursor += 2
                    continue
                if current == quote:
                    break
                buffer.append(current)
                cursor += 1
            tokens.append(("str", "".join(buffer), index))
            index = cursor + 1 if cursor < length else length
            continue
        if char.isalpha() or char in "_$":
            cursor = index + 1
            while cursor < length and (code[cursor].isalnum() or code[cursor] in "_$"):
                cursor += 1
            tokens.append(("id", code[index:cursor], index))
            index = cursor
            continue
        if char.isdigit():
            cursor = index + 1
            while cursor < length and (code[cursor].isalnum() or code[cursor] in "._"):
                cursor += 1
            tokens.append(("num", code[index:cursor], index))
            index = cursor
            continue
        tokens.append(("punct", char, index))
        index += 1
    return tokens


def _remote_literal(value: str) -> bool:
    low = value.strip().lower()
    return (
        low.startswith("http://")
        or low.startswith("https://")
        or value.strip().startswith("//")
    )


def _scan_js_tokens(
    tokens: List[Tuple[str, str, int]],
) -> Tuple[List[Tuple[int, str, str]], bool]:
    hits: List[Tuple[int, str, str]] = []
    has_seek = False
    for position, (kind, value, offset) in enumerate(tokens):
        following = tokens[position + 1] if position + 1 < len(tokens) else None
        if kind == "id" and value == "__vxSeek":
            if (
                following is not None
                and following[0] == "punct"
                and following[1] == "="
            ):
                has_seek = True
        if kind == "str" and value == "__vxSeek":
            if (
                position + 2 < len(tokens)
                and tokens[position + 1][1] == "]"
                and tokens[position + 2][1] == "="
            ):
                has_seek = True
        if kind != "id":
            continue
        followed_by_paren = (
            following is not None and following[0] == "punct" and following[1] == "("
        )
        if value == "fetch" and followed_by_paren:
            hits.append((offset, "NETWORK_JS", "fetch( is a network call"))
        elif value == "XMLHttpRequest":
            hits.append((offset, "NETWORK_JS", "XMLHttpRequest is a network call"))
        elif value in ("importScripts", "WebSocket") and followed_by_paren:
            hits.append((offset, "NETWORK_JS", f"{value}( is a network call"))
        elif value == "import" and followed_by_paren:
            argument = tokens[position + 2] if position + 2 < len(tokens) else None
            if (
                argument is not None
                and argument[0] == "str"
                and _remote_literal(argument[1])
            ):
                hits.append((offset, "NETWORK_JS", "dynamic import of a remote module"))
        elif value == "eval" and followed_by_paren:
            hits.append((offset, "DANGEROUS_JS", "eval( executes a string"))
        elif (
            value == "Function"
            and followed_by_paren
            and position > 0
            and tokens[position - 1][0] == "id"
            and tokens[position - 1][1] == "new"
        ):
            hits.append((offset, "DANGEROUS_JS", "new Function( compiles a string"))
        elif (
            value == "document"
            and position + 3 < len(tokens)
            and tokens[position + 1][1] == "."
            and tokens[position + 2][1] == "write"
            and tokens[position + 3][1] == "("
        ):
            hits.append(
                (offset, "DANGEROUS_JS", "document.write( rewrites the document")
            )
    return hits, has_seek


def _playwright_available() -> bool:
    try:
        return importlib.util.find_spec("playwright.sync_api") is not None
    except Exception:  # noqa: BLE001 - a broken install is simply "not available"
        return False


def runtime_probe(template_dir: str | Path) -> Optional[bool]:
    """Load the page and confirm `typeof window.__vxSeek === "function"`.

    Returns None when Playwright, a system browser or the page itself is
    unavailable: an unanswerable question is not a finding. Requests other than
    `file:`, `data:`, `about:` and `blob:` are aborted, so the probe never
    reaches the network.
    """
    root = _resolve_root(template_dir)
    index = root / "index.html"
    if not index.is_file():
        return None
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return None
    pw = browser = context = page = None
    try:
        pw = sync_playwright().start()
        for channel in ("chrome", "msedge"):
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                break
            except Exception:  # noqa: BLE001 - try the other system channel
                browser = None
        if browser is None:
            return None
        context = browser.new_context()
        page = context.new_page()

        def _route(route: Any) -> None:
            url = route.request.url
            if url.startswith(("file:", "data:", "about:", "blob:")):
                route.continue_()
            else:
                route.abort()

        page.route("**/*", _route)
        page.goto(index.as_uri(), wait_until="load", timeout=5000)
        return page.evaluate("typeof window.__vxSeek") == "function"
    except Exception:  # noqa: BLE001 - an unloadable page answers nothing
        return None
    finally:
        for handle in (page, context, browser):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the result
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:  # noqa: BLE001 - cleanup must not mask the result
                pass


def lint(
    template_dir: str | Path, *, runtime_check: Optional[bool] = None
) -> Dict[str, Any]:
    """Statically audit a template directory; optionally confirm the clock.

    `runtime_check=None` means "confirm with a browser when Playwright is
    installed"; pass False to keep the lint browser-free (capture does this).
    """
    root = _resolve_root(template_dir)
    linter = _Linter(root)
    index = root / "index.html"
    if index.is_file():
        label = "index.html"
        text = linter.read_text(index, label)
        if text is not None:
            parser = _HtmlScan(linter)
            parser.feed(text)
            parser.close()
    else:
        linter.add("NO_ENTRY", "index.html", 0, "index.html is absent")

    linter.lint_manifest()

    if index.is_file() and not linter.has_seek:
        linter.add(
            "CLOCK_MISSING",
            "index.html",
            0,
            "no assignment to window.__vxSeek was found",
        )

    should_probe = (
        runtime_check if runtime_check is not None else _playwright_available()
    )
    if should_probe and index.is_file() and linter.has_seek and not linter.findings:
        confirmed = runtime_probe(root)
        if confirmed is False:
            linter.add(
                "CLOCK_MISSING",
                "index.html",
                0,
                "runtime: typeof window.__vxSeek !== 'function'",
            )

    ordered = sorted(
        linter.findings,
        key=lambda item: (item["file"], item["line"], item["code"], item["detail"]),
    )
    findings = [
        {
            "code": item["code"],
            "where": f"{item['file']}:{item['line']}"
            if item["line"] > 0
            else item["file"],
            "detail": item["detail"],
        }
        for item in ordered
    ]
    return {
        "ok": not findings,
        "findings": findings,
        "stats": {
            "files": len(linter.sizes),
            "bytes": sum(linter.sizes.values()),
            "deps": linter.deps,
        },
    }


def lint_or_raise(
    template_dir: str | Path, *, runtime_check: Optional[bool] = None
) -> Dict[str, Any]:
    """Run `lint`; on any finding raise `MotionLintError` carrying all of them."""
    result = lint(template_dir, runtime_check=runtime_check)
    if not result["ok"]:
        raise MotionLintError(template_dir, result["findings"])
    return result
