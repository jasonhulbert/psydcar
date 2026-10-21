"""Probe a dashboard-generated connection against a release-installed MCP server."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen


def request_json(url: str, data: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Psydcar-Token"] = token
        headers["Origin"] = url.split("/api/", 1)[0]
    request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers=headers)
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def save(url: str, home: Path, store: Path, sidecar_id: str, output: Path) -> None:
    token = request_json(url + "/api/bootstrap")["token"]
    preview = request_json(url + "/api/connections/preview",
                           {"name": "release_probe", "sidecar_ids": [sidecar_id]}, token)
    entry = preview["config"]["mcpServers"]["release_probe"]
    assert preview["selected"] == [{"id": sidecar_id, "name": "Release test"}]
    assert entry == {
        "command": str(home / ".local/bin/psydcar"),
        "args": ["mcp", "--sidecar-id", sidecar_id],
        "env": {"PSYDCAR_HOME": str(store.resolve())},
    }, entry
    output.write_text(json.dumps(preview["config"]))


def check(config_path: Path, home: Path, store: Path, sidecar_id: str) -> None:
    entry = json.loads(config_path.read_text())["mcpServers"]["release_probe"]
    assert entry["command"] == str(home / ".local/bin/psydcar")
    assert entry["env"] == {"PSYDCAR_HOME": str(store.resolve())}
    environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", **entry["env"]}
    process = subprocess.Popen([entry["command"], *entry["args"]],
                               cwd=config_path.parent, env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, bufsize=1)
    responses: queue.Queue[str] = queue.Queue()
    assert process.stdin is not None and process.stdout is not None
    threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True).start()

    def send(payload: dict) -> None:
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    def call(request_id: int, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        send(payload)
        while True:
            try:
                response = json.loads(responses.get(timeout=15))
            except queue.Empty as exc:
                raise AssertionError(f"MCP server did not respond to {method}; exit={process.poll()}") from exc
            if response.get("id") == request_id:
                return response

    try:
        initialized = call(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "release-probe", "version": "1"},
        })
        assert initialized["result"]["serverInfo"]["name"] == "psydcar", initialized
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = call(2, "tools/list")["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"search", "retrieve"}
        found = call(3, "tools/call", {"name": "search", "arguments": {"query": "searchable"}})
        results = found["result"]["structuredContent"]["results"]
        assert results and {hit["sidecar"]["id"] for hit in results} == {sidecar_id}, found
        retrieved = call(4, "tools/call", {"name": "retrieve", "arguments": {"evidence_id": results[0]["evidence_id"]}})
        assert retrieved["result"]["structuredContent"]["context_origin"] == "source", retrieved
        private = call(5, "tools/call", {"name": "search", "arguments": {"query": "privateonly"}})
        assert private["result"]["structuredContent"]["results"] == [], private
    finally:
        process.terminate()
        process.communicate(timeout=5)


if __name__ == "__main__":
    mode, *arguments = sys.argv[1:]
    if mode == "save":
        url, home, store, sidecar_id, output = arguments
        save(url, Path(home), Path(store), sidecar_id, Path(output))
    elif mode == "check":
        config_path, home, store, sidecar_id = arguments
        check(Path(config_path), Path(home), Path(store), sidecar_id)
    else:
        raise SystemExit("Usage: check-release-mcp.py save URL HOME STORE SIDECAR_ID OUTPUT | check CONFIG HOME STORE SIDECAR_ID")
