"""Zero-dependency MCP server over stdio, backed only by :mod:`vx.api`.

One JSON object per line on stdin/stdout, UTF-8. Nothing but responses ever
reaches stdout; diagnostics go to stderr with an ``[mcp]`` prefix. The loop is
blocking on purpose: no asyncio, no anyio, no third-party SDK.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import api, contract

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "vx-video-editor"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
NOT_INITIALIZED = -32002

DOCTOR_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

TRIAGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1},
        "manifest": {"type": "string"},
        "require_anchor": {"type": "boolean", "default": False},
    },
    "required": ["path", "width", "height"],
    "additionalProperties": False,
}

EDL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {"type": ["object", "string"]},
    },
    "required": ["plan"],
    "additionalProperties": False,
}

AUTO_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "assets": {"type": "string"},
        "out": {"type": "string"},
        "workdir": {"type": "string"},
        "fps": {"type": "number"},
        "manifest": {"type": "string"},
        "autonomous": {"type": "boolean", "default": False},
        "crf": {"type": "integer"},
        "preset": {"type": "string"},
    },
    "required": ["prompt", "assets", "out"],
    "additionalProperties": False,
}


def _tool_doctor(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return api.doctor()


def _tool_triage(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return api.triage(
        arguments["path"],
        arguments["width"],
        arguments["height"],
        manifest=arguments.get("manifest"),
        require_anchor=arguments.get("require_anchor", False),
    )


def _tool_edl_validate(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return api.validate_edl(arguments["plan"])


def _tool_auto(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return api.run_auto(api.AutoRequest(**arguments))


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "vx_doctor",
        "description": (
            "Scan the environment and return the verdict (ready, degraded or "
            "broken) together with the measured components."
        ),
        "inputSchema": DOCTOR_SCHEMA,
        "handler": _tool_doctor,
    },
    {
        "name": "vx_triage",
        "description": (
            "Triage the first level of a directory, read-only. Every rejection "
            "carries a reason and not_evaluated names what was not measured."
        ),
        "inputSchema": TRIAGE_SCHEMA,
        "handler": _tool_triage,
    },
    {
        "name": "vx_edl_validate",
        "description": (
            "Validate an EDL document supplied inline as an object, as a JSON "
            "string, or as a file path."
        ),
        "inputSchema": EDL_SCHEMA,
        "handler": _tool_edl_validate,
    },
    {
        "name": "vx_auto",
        "description": (
            "Run the whole pipeline synchronously from a prompt and an asset "
            "folder. This call can run for minutes: the client owns the "
            "timeout. It writes plan.json, auto_report.json and <workdir>/out. "
            "For interactive clients the CLI is preferable."
        ),
        "inputSchema": AUTO_SCHEMA,
        "handler": _tool_auto,
    },
]

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _log(message: str) -> None:
    sys.stderr.write(f"[mcp] {message}\n")
    sys.stderr.flush()


def _write(message: Mapping[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _request_id(message: Any) -> Any:
    if not isinstance(message, dict):
        return None
    value = message.get("id")
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return value
    return None


def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "array":
        return isinstance(value, list)
    if name == "null":
        return value is None
    return True


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _validate(value: Any, schema: Mapping[str, Any], path: str) -> Optional[str]:
    """Return a message naming the offending field, or ``None`` when valid."""
    declared = schema.get("type")
    if declared is not None:
        allowed = declared if isinstance(declared, list) else [declared]
        if not any(_is_type(value, name) for name in allowed):
            expected = " or ".join(str(name) for name in allowed)
            return f"{path}: expected {expected}, got {_type_name(value)}"

    if isinstance(value, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key in schema.get("required") or []:
                if key not in value:
                    return f"{path}.{key}: required property missing"
            if schema.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        return f"{path}.{key}: unexpected property"
            for key, sub_schema in properties.items():
                if key in value:
                    problem = _validate(value[key], sub_schema, f"{path}.{key}")
                    if problem is not None:
                        return problem

    minimum = schema.get("minimum")
    if (
        minimum is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        if value < minimum:
            return f"{path}: must be >= {minimum}, got {value}"

    return None


def _initialize_result(params: Any) -> Dict[str, Any]:
    requested = None
    if isinstance(params, dict):
        requested = params.get("protocolVersion")
    version = (
        requested
        if isinstance(requested, str) and requested in PROTOCOL_VERSIONS
        else DEFAULT_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": contract.VERSION},
    }


def _tool_result(payload: Any) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ],
        "isError": False,
    }


def _tool_failure(exc: BaseException) -> Dict[str, Any]:
    payload = {"error": str(exc), "type": type(exc).__name__}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ],
        "isError": True,
    }


def _handle_tools_call(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        return _error(None, INVALID_PARAMS, "params: expected object")
    name = params.get("name")
    if not isinstance(name, str):
        return _error(None, INVALID_PARAMS, "params.name: expected string")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return _error(None, INVALID_PARAMS, "params.arguments: expected object")
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return _error(None, INVALID_PARAMS, f"params.name: unknown tool {name!r}")
    problem = _validate(arguments, tool["inputSchema"], "arguments")
    if problem is not None:
        return _error(None, INVALID_PARAMS, problem)
    try:
        payload = tool["handler"](arguments)
    except Exception as exc:  # noqa: BLE001 - a domain failure is a result
        _log(f"tool {name} failed: {type(exc).__name__}: {exc}")
        return _tool_failure(exc)
    return _tool_result(payload)


def _handle(message: Any, session: Dict[str, bool]) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "Invalid Request: expected an object")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(
            _request_id(message),
            INVALID_REQUEST,
            'Invalid Request: jsonrpc must be "2.0" and method must be a string',
        )

    method = message["method"]
    if "id" not in message:
        if method == "notifications/initialized":
            session["initialized"] = True
        return None

    request_id = message.get("id")
    params = message.get("params")

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _initialize_result(params),
        }

    if method in ("tools/list", "tools/call"):
        if not session["initialized"]:
            return _error(request_id, NOT_INITIALIZED, "Server not initialized")
        if method == "tools/list":
            listing = [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["inputSchema"],
                }
                for tool in TOOLS
            ]
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": listing},
            }
        response = _handle_tools_call(params)
        if "error" in response:
            response["jsonrpc"] = "2.0"
            response["id"] = request_id
            return response
        return {"jsonrpc": "2.0", "id": request_id, "result": response}

    return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    del argv  # no flags: the transport is the interface
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - captured or older streams
            pass

    session = {"initialized": False}
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"parse error: {exc.msg}")
            _write(_error(None, PARSE_ERROR, f"Parse error: {exc.msg}"))
            continue
        response = _handle(message, session)
        if response is not None:
            _write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
