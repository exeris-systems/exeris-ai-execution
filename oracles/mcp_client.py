"""A minimal MCP client over stdio, enough for an oracle to read a context adapter's tools.

ADR-086 §A.3 admits `exeris-ai-bridge` to this layer as a context adapter: the layer consumes what
the bridge publishes, and nothing about the layer is added to the bridge. This module is that
consumption and nothing more — one child process, JSON-RPC 2.0 over newline-delimited stdio, the
`initialize` handshake, and `tools/call`. It is standard library only, because the oracle it serves
runs in jobs that install nothing.

Every failure to reach an answer is an `McpError`: a server that did not start, did not answer in
time, closed its stream or answered with a protocol error. A caller composes that as a gate that
could not run, never as a finding — the server saying nothing is not the registry saying no.

The child is always terminated when the client closes, whatever state it is in, so a judgement
never leaves a server running behind it.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading

from .paths import PATH_GRAMMAR

PROTOCOL_VERSION = "2025-06-18"
METHOD, ERROR = "method", "error"
CLIENT_INFO = {"name": "exeris-ai-execution-oracle", "version": "1"}
#: How long the handshake and each call may take before the server is taken to have said nothing.
DEFAULT_TIMEOUT = 30.0

#: Which interpreter runs a server, chosen by the server file's extension. The executable comes from
#: this table and never from the value a caller passed: a server is a file handed to a known runtime.
LAUNCHERS = {".js": "node", ".mjs": "node", ".cjs": "node", ".py": sys.executable}


class McpError(Exception):
    """The server could not be reached, or answered outside the protocol."""


def launcher_for(server: str) -> str | None:
    """The interpreter that runs `server`, or None for a file this client does not know how to run."""
    return LAUNCHERS.get(os.path.splitext(server)[1])


def _pump(stream, sink: queue.Queue) -> None:
    """Move the child's stdout into `sink` one line at a time; None marks the end of the stream."""
    for line in stream:
        sink.put(line)
    sink.put(None)


class McpClient:
    """One MCP server as a child process, used as a context manager.

    `with McpClient(server, env) as client:` starts the server and completes the handshake on entry
    and terminates it on exit; `client.call_tool(name, arguments)` returns the tool's result.
    """

    def __init__(self, server: str, env: dict[str, str], timeout: float = DEFAULT_TIMEOUT):
        self._server = server
        self._env = env
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._stderr = None
        self._next_id = 0
        self.server_info: dict = {}

    # -- lifecycle --------------------------------------------------------------------------

    def __enter__(self) -> "McpClient":
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        launcher = launcher_for(self._server)
        if launcher is None:
            raise McpError(f"no runtime is known for {self._server}")
        # The server path is the one value on the command line this client did not choose, so it is
        # held to the grammar of a path in a checkout before it gets there.
        if not PATH_GRAMMAR.fullmatch(self._server):
            raise McpError(f"the server path is not one this client starts ({self._server})")
        if not os.path.isfile(self._server):
            raise McpError(f"the server is not on disk ({self._server})")
        self._stderr = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                [launcher, self._server], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr, env=self._env, text=True, encoding="utf-8")
        except OSError as exc:
            raise McpError(f"the server did not start: {exc}") from exc
        threading.Thread(target=_pump, args=(self._proc.stdout, self._lines), daemon=True).start()
        result = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                             "capabilities": {}, "clientInfo": CLIENT_INFO})
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        self.notify("notifications/initialized")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    # -- the wire ---------------------------------------------------------------------------

    def _stderr_tail(self, limit: int = 300) -> str:
        if self._stderr is None:
            return ""
        self._stderr.seek(0)
        text = self._stderr.read().decode("utf-8", "replace").strip()
        return text[-limit:]

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("the server is not running")
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            raise McpError(f"the server closed its input: {self._stderr_tail()}") from exc

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", METHOD: method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _receive(self, want: int):
        """The response to request `want`, skipping the server's own notifications and requests."""
        while True:
            try:
                line = self._lines.get(timeout=self._timeout)
            except queue.Empty as exc:
                raise McpError(f"no answer within {self._timeout:g}s") from exc
            if line is None:
                raise McpError(f"the server closed its output: {self._stderr_tail()}")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == want and METHOD not in message:
                return message

    def request(self, method: str, params: dict | None = None):
        self._next_id += 1
        want = self._next_id
        message = {"jsonrpc": "2.0", "id": want, METHOD: method}
        if params is not None:
            message["params"] = params
        self._send(message)
        answer = self._receive(want)
        if ERROR in answer:
            error = answer[ERROR] if isinstance(answer[ERROR], dict) else {}
            raise McpError(f"{method}: {error.get('message', answer[ERROR])}")
        return answer.get("result")

    def call_tool(self, name: str, arguments: dict) -> tuple[bool, object]:
        """`(is_error, value)` for one tool call.

        The value is the text of the result's content joined, parsed as JSON where it parses and
        left as text where it does not. `is_error` is the tool's own statement that the call did not
        succeed, which is an answer and not a protocol failure: a tool reporting that a record does
        not exist has told the caller something.
        """
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise McpError(f"{name}: the result is not an object")
        text = "".join(part.get("text", "") for part in result.get("content", [])
                       if isinstance(part, dict) and part.get("type") == "text")
        try:
            value = json.loads(text)
        except ValueError:
            value = text
        return bool(result.get("isError")), value
