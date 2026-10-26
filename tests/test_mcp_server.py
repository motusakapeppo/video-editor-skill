"""MCP server over stdio: envelope, tool schemas, domain failures, stdout purity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _plan_doc() -> dict:
    return {
        "contract_version": "2.2.0",
        "timeline": {
            "fps": 25.0,
            "duration_sec": 2.0,
            "aspect_ratio": "9:16",
            "resolution": [1080, 1920],
        },
        "sources": {"src_01": {"path": "a.mp4", "sha256": "a" * 64, "duration": 2.0}},
        "tracks": {
            "video": [
                {
                    "track_id": 0,
                    "role": "main",
                    "clips": [
                        {
                            "clip_id": "c1",
                            "source_id": "src_01",
                            "src_in": 0.0,
                            "src_out": 2.0,
                            "dst_in": 0.0,
                            "dst_out": 2.0,
                            "layer": 0,
                            "role": "main_talking_head",
                        }
                    ],
                }
            ],
            "audio": [],
        },
    }


def _request(method: str, request_id: object = None, params: object = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


def _call(name: str, arguments: dict, request_id: object) -> dict:
    return _request("tools/call", request_id, {"name": name, "arguments": arguments})


def _result(response: object) -> dict:
    if not isinstance(response, dict):
        return {}
    value = response.get("result")
    return value if isinstance(value, dict) else {}


def _error_code(response: object) -> object:
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _text(response: object) -> str:
    content = _result(response).get("content") or []
    if not content or not isinstance(content[0], dict):
        return ""
    return str(content[0].get("text", ""))


def _payload(response: object) -> dict:
    try:
        value = json.loads(_text(response))
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _build_requests(tmp: Path) -> tuple:
    empty = tmp / "empty"
    empty.mkdir()
    ghost = tmp / "ghost_dir"
    good = _plan_doc()
    broken = _plan_doc()
    broken["tracks"]["video"][0]["clips"][0]["src_out"] = 0.0

    requests = [
        '{"jsonrpc":"2.0","method":"ping"',
        _request("ping", 1),
        _request("tools/list", 2),
        _request("initialize", 3, {"protocolVersion": "2024-11-05"}),
        _request("initialize", 4, {"protocolVersion": "1999-01-01"}),
        _request("notifications/initialized"),
        _request("notifications/whatever"),
        _request("tools/list", 5),
        _call("vx_doctor", {}, 6),
        _call("vx_triage", {"path": str(empty), "width": 1080, "height": 1920}, 7),
        _call("vx_triage", {"path": str(ghost), "width": 1080, "height": 1920}, 8),
        _call("vx_edl_validate", {"plan": good}, 9),
        _call("vx_edl_validate", {"plan": broken}, 10),
        _call("vx_auto", {"prompt": "p", "out": "o"}, 11),
        _call("vx_no_such_tool", {}, 12),
        _request("no/such/method", 13),
    ]
    return requests, empty, ghost


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-mcp-") as raw:
        tmp = Path(raw)
        requests, empty, ghost = _build_requests(tmp)

        lines = [
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            for item in requests
        ]
        payload = "".join(line + "\n" for line in lines)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "scripts")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "vx.mcp_server"],
            cwd=str(ROOT),
            env=env,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )

        check("exit code 0", proc.returncode == 0, proc.returncode)

        raw_lines = [line for line in proc.stdout.splitlines() if line.strip()]
        responses = []
        parsed_all = True
        for line in raw_lines:
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                parsed_all = False

        check(
            "stdout: ogni riga e' JSON",
            parsed_all and len(raw_lines) == len(responses),
            (len(raw_lines), len(responses)),
        )
        check(
            "stdout: nessuna riga inizia con [",
            all(not line.lstrip().startswith("[") for line in raw_lines),
            raw_lines[:1],
        )

        by_id = {}
        for response in responses:
            if isinstance(response, dict) and "id" in response:
                by_id[response["id"]] = response

        parse_error = by_id.get(None)
        check(
            "JSON invalido -> -32700",
            _error_code(parse_error) == -32700,
            _error_code(parse_error),
        )
        check(
            "JSON invalido -> id null",
            isinstance(parse_error, dict) and parse_error.get("id") is None,
            parse_error,
        )

        check(
            "ping prima di initialize -> {}",
            _result(by_id.get(1)) == {},
            _result(by_id.get(1)),
        )
        check(
            "tools/list pre-init -> -32002",
            _error_code(by_id.get(2)) == -32002,
            _error_code(by_id.get(2)),
        )

        init = _result(by_id.get(3))
        check(
            "initialize 2024-11-05 echo",
            init.get("protocolVersion") == "2024-11-05",
            init.get("protocolVersion"),
        )
        check(
            "initialize capabilities.tools",
            isinstance((init.get("capabilities") or {}).get("tools"), dict),
            init.get("capabilities"),
        )
        info = init.get("serverInfo") or {}
        check(
            "serverInfo.name", info.get("name") == "vx-video-editor", info.get("name")
        )
        check(
            "serverInfo.version",
            info.get("version") == contract.VERSION,
            info.get("version"),
        )

        fallback = _result(by_id.get(4))
        check(
            "initialize 1999-01-01 -> 2025-06-18",
            fallback.get("protocolVersion") == "2025-06-18",
            fallback.get("protocolVersion"),
        )

        expected = 1 + sum(
            1 for item in requests if isinstance(item, dict) and "id" in item
        )
        check(
            "notifiche senza risposta (conteggio)",
            len(responses) == expected,
            (len(responses), expected),
        )
        check(
            "notifiche senza risposta (id assente)",
            sum(
                1
                for response in responses
                if isinstance(response, dict) and response.get("id") is None
            )
            == 1,
            [
                response.get("id")
                for response in responses
                if isinstance(response, dict)
            ],
        )

        listing = _result(by_id.get(5))
        tools = listing.get("tools") or []
        names = [tool.get("name") for tool in tools]
        check(
            "tools/list quattro nomi in ordine",
            names == ["vx_doctor", "vx_triage", "vx_edl_validate", "vx_auto"],
            names,
        )
        check(
            "tools/list inputSchema.type object",
            all(
                (tool.get("inputSchema") or {}).get("type") == "object"
                for tool in tools
            ),
            [tool.get("inputSchema", {}).get("type") for tool in tools],
        )

        doctor = _result(by_id.get(6))
        check(
            "vx_doctor isError false",
            doctor.get("isError") is False,
            doctor.get("isError"),
        )
        check(
            "vx_doctor verdict",
            "verdict" in _payload(by_id.get(6)),
            _payload(by_id.get(6)).get("verdict"),
        )

        empty_result = _result(by_id.get(7))
        check(
            "vx_triage vuota isError false",
            empty_result.get("isError") is False,
            empty_result.get("isError"),
        )
        check(
            "vx_triage vuota total 0",
            _payload(by_id.get(7)).get("total") == 0,
            _payload(by_id.get(7)).get("total"),
        )

        ghost_result = _result(by_id.get(8))
        check(
            "vx_triage inesistente isError true",
            ghost_result.get("isError") is True,
            ghost_result.get("isError"),
        )
        check(
            "vx_triage inesistente nomina path",
            str(ghost) in str(_payload(by_id.get(8)).get("error", "")),
            _payload(by_id.get(8)).get("error"),
        )

        good_result = _result(by_id.get(9))
        check(
            "vx_edl_validate valido isError false",
            good_result.get("isError") is False,
            good_result.get("isError"),
        )
        check(
            "vx_edl_validate valido ok true",
            _payload(by_id.get(9)).get("ok") is True,
            _payload(by_id.get(9)).get("errors"),
        )

        broken_result = _result(by_id.get(10))
        check(
            "vx_edl_validate rotto isError false",
            broken_result.get("isError") is False,
            broken_result.get("isError"),
        )
        check(
            "vx_edl_validate rotto ok false",
            _payload(by_id.get(10)).get("ok") is False,
            _payload(by_id.get(10)).get("ok"),
        )
        check(
            "vx_edl_validate rotto errors non vuoto",
            bool(_payload(by_id.get(10)).get("errors")),
            _payload(by_id.get(10)).get("errors"),
        )

        check(
            "vx_auto senza assets -> -32602",
            _error_code(by_id.get(11)) == -32602,
            _error_code(by_id.get(11)),
        )
        check(
            "tool sconosciuto -> -32602",
            _error_code(by_id.get(12)) == -32602,
            _error_code(by_id.get(12)),
        )
        check(
            "metodo sconosciuto -> -32601",
            _error_code(by_id.get(13)) == -32601,
            _error_code(by_id.get(13)),
        )

    cli_requests = [
        _request("initialize", 1, {"protocolVersion": "2025-06-18"}),
        _request("notifications/initialized"),
        _request("tools/list", 2),
        _request("ping", 3),
    ]
    cli_payload = "".join(
        json.dumps(item, ensure_ascii=False) + "\n" for item in cli_requests
    )
    cli_env = os.environ.copy()
    cli_env["PYTHONPATH"] = str(ROOT / "scripts")
    cli_env["PYTHONIOENCODING"] = "utf-8"
    cli_env["PYTHONUTF8"] = "1"
    cli_proc = subprocess.run(
        [sys.executable, "-m", "vx.cli", "mcp"],
        cwd=str(ROOT),
        env=cli_env,
        input=cli_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )

    check("vx mcp exit code 0", cli_proc.returncode == 0, cli_proc.returncode)

    cli_raw = [line for line in cli_proc.stdout.splitlines() if line.strip()]
    cli_responses = []
    cli_parsed_all = True
    for line in cli_raw:
        try:
            cli_responses.append(json.loads(line))
        except json.JSONDecodeError:
            cli_parsed_all = False
    check(
        "vx mcp stdout: ogni riga e' JSON",
        cli_parsed_all and len(cli_raw) == len(cli_responses),
        (len(cli_raw), len(cli_responses)),
    )

    cli_by_id = {}
    for response in cli_responses:
        if isinstance(response, dict) and "id" in response:
            cli_by_id[response["id"]] = response

    cli_init = _result(cli_by_id.get(1))
    cli_info = cli_init.get("serverInfo") or {}
    check(
        "vx mcp serverInfo.name",
        cli_info.get("name") == "vx-video-editor",
        cli_info.get("name"),
    )

    cli_names = [
        tool.get("name") for tool in (_result(cli_by_id.get(2)).get("tools") or [])
    ]
    check(
        "vx mcp tools/list quattro nomi in ordine",
        cli_names == ["vx_doctor", "vx_triage", "vx_edl_validate", "vx_auto"],
        cli_names,
    )

    check(
        "vx mcp ping -> {}",
        _result(cli_by_id.get(3)) == {},
        _result(cli_by_id.get(3)),
    )

    deep = "[" * 20000 + "]" * 20000
    resilience_payload = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
        '{"name":"vx_edl_validate","arguments":{"plan":' + deep + "}}}\n"
        '{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
    )
    resilience = subprocess.run(
        [sys.executable, "-m", "vx.mcp_server"],
        cwd=str(ROOT),
        env=cli_env,
        input=resilience_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    resilience_responses = []
    for line in resilience.stdout.splitlines():
        if line.strip():
            resilience_responses.append(json.loads(line))
    check(
        "JSON profondo: il server sopravvive",
        resilience.returncode == 0 and len(resilience_responses) == 2,
        (resilience.returncode, len(resilience_responses)),
    )
    check(
        "JSON profondo: errore dichiarato",
        len(resilience_responses) == 2
        and resilience_responses[0].get("error", {}).get("code") == -32700,
        resilience_responses[0].get("error") if resilience_responses else None,
    )
    check(
        "JSON profondo: il ping successivo risponde",
        any(
            item.get("id") == 2 and item.get("result") == {}
            for item in resilience_responses
        ),
        resilience_responses[-1] if resilience_responses else None,
    )

    ordering_payload = (
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    )
    ordering = subprocess.run(
        [sys.executable, "-m", "vx.mcp_server"],
        cwd=str(ROOT),
        env=cli_env,
        input=ordering_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    ordering_responses = [
        json.loads(line) for line in ordering.stdout.splitlines() if line.strip()
    ]
    check(
        "notifica senza initialize non sblocca",
        len(ordering_responses) == 1
        and ordering_responses[0].get("error", {}).get("code") == -32002,
        ordering_responses,
    )

    argv_proc = subprocess.run(
        [sys.executable, "-m", "vx.mcp_server", "--help"],
        cwd=str(ROOT),
        env=cli_env,
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    check(
        "vx-mcp rifiuta argomenti",
        argv_proc.returncode == 2 and argv_proc.stdout.strip() == "",
        (argv_proc.returncode, argv_proc.stdout[:40]),
    )
    check(
        "vx-mcp nomina gli argomenti su stderr",
        "arguments" in argv_proc.stderr.lower(),
        argv_proc.stderr.strip()[:60],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
