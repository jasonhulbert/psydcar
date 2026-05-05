import json
from io import StringIO

import pytest

from psydecar.index import connect_index, index_path_for_sidecar, rebuild_sidecar_index
from psydecar.mcp import PsydecarMcpServer, McpError, parse_sidecar_ids
from psydecar.sidecars import SidecarRegistry


def test_mcp_initialize_and_tools_list(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry.create(source_root, sidecar_id="docs")
    server = PsydecarMcpServer(registry, ["docs"])

    initialize = server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert initialize is not None
    assert initialize["result"]["serverInfo"]["name"] == "psydecar"
    assert initialize["result"]["capabilities"]["tools"]["listChanged"] is False

    tools = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools is not None
    assert {tool["name"] for tool in tools["result"]["tools"]} == {
        "search",
        "read_chunk",
        "read_file",
        "list_files",
        "get_status",
    }


def test_mcp_search_labels_multi_sidecar_results(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
    (frontend / "app.md").write_text("# App\nneedle frontend\n", encoding="utf-8")
    (backend / "api.md").write_text("# Api\nneedle backend\n", encoding="utf-8")
    registry.create(frontend, sidecar_id="frontend")
    registry.create(backend, sidecar_id="backend")
    rebuild_sidecar_index(registry, "frontend")
    rebuild_sidecar_index(registry, "backend")

    server = PsydecarMcpServer(registry, ["frontend", "backend"])
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"query": "needle", "mode": "keyword", "limit": 10},
            },
        }
    )
    assert response is not None

    results = response["result"]["structuredContent"]["results"]
    assert {result["sidecar_id"] for result in results} == {"frontend", "backend"}
    assert {result["relative_path"] for result in results} == {"app.md", "api.md"}


def test_mcp_read_chunk_and_read_file(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "notes.md").write_text("# Notes\nneedle docs\n", encoding="utf-8")
    registry.create(source_root, sidecar_id="docs")
    rebuild_sidecar_index(registry, "docs")

    with connect_index(index_path_for_sidecar(registry, "docs")) as connection:
        chunk_id = int(connection.execute("SELECT id FROM chunks").fetchone()["id"])

    server = PsydecarMcpServer(registry, ["docs"])
    chunk_response = server.call_tool(
        {"name": "read_chunk", "arguments": {"sidecar_id": "docs", "chunk_id": chunk_id}}
    )
    assert "needle docs" in chunk_response["structuredContent"]["text"]

    file_response = server.call_tool(
        {"name": "read_file", "arguments": {"sidecar_id": "docs", "path": "notes.md"}}
    )
    assert file_response["structuredContent"]["text"] == "# Notes\nneedle docs\n"


def test_mcp_read_file_rejects_path_traversal(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    source_root = tmp_path / "source"
    source_root.mkdir()
    (tmp_path / "secret.md").write_text("secret\n", encoding="utf-8")
    registry.create(source_root, sidecar_id="docs")
    server = PsydecarMcpServer(registry, ["docs"])

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"sidecar_id": "docs", "path": "../secret.md"},
            },
        }
    )
    assert response is not None

    assert response["error"]["code"] == -32602
    assert "escapes sidecar source root" in response["error"]["message"]


def test_mcp_list_files_and_get_status(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    source_root = tmp_path / "source"
    nested = source_root / "docs"
    nested.mkdir(parents=True)
    (nested / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (source_root / "ignored.png").write_bytes(b"png")
    registry.create(source_root, sidecar_id="docs")
    rebuild_sidecar_index(registry, "docs")
    server = PsydecarMcpServer(registry, ["docs"])

    files = server.call_tool(
        {"name": "list_files", "arguments": {"sidecar_id": "docs", "prefix": "docs"}}
    )["structuredContent"]["files"]
    assert files == [{"relative_path": "docs/guide.md", "extension": ".md", "size_bytes": 8}]

    status = server.call_tool({"name": "get_status", "arguments": {"sidecar_id": "docs"}})
    sidecar = status["structuredContent"]["sidecars"][0]["sidecar"]
    assert sidecar["id"] == "docs"
    assert sidecar["indexing_status"] == "indexed"


def test_mcp_status_and_search_do_not_create_missing_index(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry.create(source_root, sidecar_id="docs")
    server = PsydecarMcpServer(registry, ["docs"])
    index_path = index_path_for_sidecar(registry, "docs")

    status = server.call_tool({"name": "get_status", "arguments": {"sidecar_id": "docs"}})
    search = server.call_tool(
        {"name": "search", "arguments": {"query": "needle", "mode": "keyword"}}
    )

    assert status["structuredContent"]["sidecars"][0]["errors"] == []
    assert search["structuredContent"]["results"] == []
    assert not index_path.exists()


def test_mcp_stdio_server_writes_jsonrpc_response(tmp_path):
    registry = SidecarRegistry(tmp_path / "storage")
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry.create(source_root, sidecar_id="docs")
    server = PsydecarMcpServer(registry, ["docs"])
    stdin = StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
    stdout = StringIO()

    server.serve(stdin=stdin, stdout=stdout)

    response = json.loads(stdout.getvalue())
    assert response["id"] == 1
    assert response["result"]["tools"][0]["name"] == "search"


def test_parse_sidecar_ids_rejects_empty_list():
    with pytest.raises(McpError, match="at least one"):
        parse_sidecar_ids(" , ")
