#!/usr/bin/env python3
"""A stand-in for the Exeris MCP server, answering the two tools the docs oracle calls.

It speaks the same newline-delimited JSON-RPC over stdio as the real server and answers from
`registry.json` in the directory `EXERIS_DOCS_ROOT` names, so a case controls what the registry
says by writing that file:

    {"rows": [{"number": 1, "title": "...", "owningRepo": "..."}],
     "records": {"1": "<markdown of ADR-001>"},
     "crash": false}

`docs-list_adrs` returns `rows`; `docs-get_adr` returns the record's markdown, or the tool error the
real server gives for a number the registry does not hold. A missing `registry.json` makes
`docs-list_adrs` a tool error, and `"crash": true` makes the server exit before the handshake —
the two ways a bridge can fail to answer.
"""
import json
import os
import sys


def _registry() -> dict | None:
    path = os.path.join(os.environ.get("EXERIS_DOCS_ROOT", ""), "registry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _text(text: str, is_error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _call(registry: dict | None, name: str, arguments: dict) -> dict:
    if registry is None:
        return _text("the registry is not readable", True)
    if name == "docs-list_adrs":
        return _text(json.dumps(registry.get("rows", [])))
    if name == "docs-get_adr":
        number = arguments.get("number")
        record = registry.get("records", {}).get(str(number))
        if record is None:
            return _text(f"ADR-{number} is not in the registry", True)
        return _text(record)
    return _text(f"unknown tool {name}", True)


def main() -> int:
    registry = _registry()
    if registry is not None and registry.get("crash"):
        return 3
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "stub-bridge", "version": "0.0.0"}}
        elif method == "tools/call":
            params = message.get("params", {})
            result = _call(registry, params.get("name"), params.get("arguments", {}))
        else:
            reply = {"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32601, "message": f"no method {method}"}}
            print(json.dumps(reply), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
