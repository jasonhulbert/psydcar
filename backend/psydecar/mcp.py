"""Minimal MCP stdio server for read-only sidecar access."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from psydecar import __version__
from psydecar.filesystem import resolve_root_relative_path
from psydecar.index import (
    IndexingErrorRecord,
    SearchResult,
    build_fts_query,
    index_path_for_sidecar,
    list_vector_chunks,
    read_text_content,
    text_preview,
)
from psydecar.scanner import scan_files
from psydecar.search import DEFAULT_SEARCH_LIMIT, merge_search_results, normalize_limit
from psydecar.sidecars import Sidecar, SidecarRegistry

MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpError(ValueError):
    """Raised when an MCP request or tool call is invalid."""


class PsydecarMcpServer:
    """Small JSON-RPC MCP server exposing read-only sidecar tools."""

    def __init__(self, registry: SidecarRegistry, sidecar_ids: Iterable[str]) -> None:
        self.registry = registry
        self.sidecar_ids = tuple(dict.fromkeys(sidecar_ids))
        if not self.sidecar_ids:
            raise McpError("at least one sidecar id is required")

        for sidecar_id in self.sidecar_ids:
            self.registry.get(sidecar_id)

    def serve(self, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        """Serve newline-delimited JSON-RPC requests over stdio."""

        for line in stdin:
            if not line.strip():
                continue
            response = self.handle_line(line)
            if response is not None:
                stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                stdout.flush()

    def handle_line(self, line: str) -> dict[str, Any] | None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            return jsonrpc_error(None, PARSE_ERROR, f"invalid JSON: {exc.msg}")
        return self.handle_message(message)

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return jsonrpc_error(None, INVALID_REQUEST, "request must be a JSON object")

        request_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return jsonrpc_error(request_id, INVALID_REQUEST, "request method is required")

        if method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                return jsonrpc_result(request_id, self.initialize_result())
            if method == "tools/list":
                return jsonrpc_result(request_id, {"tools": MCP_TOOLS})
            if method == "tools/call":
                return jsonrpc_result(request_id, self.call_tool(message.get("params")))
            if method == "ping":
                return jsonrpc_result(request_id, {})
        except ValueError as exc:
            return jsonrpc_error(request_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - protocol boundary must return JSON-RPC errors.
            return jsonrpc_error(request_id, INTERNAL_ERROR, str(exc))

        return jsonrpc_error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    def initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "psydecar", "version": __version__},
        }

    def call_tool(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise McpError("tools/call params must be an object")

        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool_name, str):
            raise McpError("tool name is required")
        if not isinstance(arguments, dict):
            raise McpError("tool arguments must be an object")

        if tool_name == "search":
            payload = self.tool_search(arguments)
        elif tool_name == "read_chunk":
            payload = self.tool_read_chunk(arguments)
        elif tool_name == "read_file":
            payload = self.tool_read_file(arguments)
        elif tool_name == "list_files":
            payload = self.tool_list_files(arguments)
        elif tool_name == "get_status":
            payload = self.tool_get_status(arguments)
        else:
            raise McpError(f"unknown tool: {tool_name}")

        return {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
            "structuredContent": payload,
            "isError": False,
        }

    def tool_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = require_string(arguments, "query")
        mode = optional_string(arguments, "mode", default="hybrid")
        limit = optional_positive_int(arguments, "limit", default=DEFAULT_SEARCH_LIMIT)
        target_sidecar_ids = self.target_sidecar_ids(arguments)

        results: list[dict[str, Any]] = []
        for sidecar_id in target_sidecar_ids:
            sidecar = self.registry.get(sidecar_id)
            sidecar_results = readonly_search_sidecar(self.registry, sidecar.id, query, mode, limit)
            results.extend(
                {
                    "sidecar_id": sidecar.id,
                    "sidecar_name": sidecar.name,
                    **search_result_to_dict(result),
                }
                for result in sidecar_results
            )

        results.sort(key=lambda result: float(result["score"]), reverse=True)
        return {"query": query, "mode": mode, "results": results[:limit]}

    def tool_read_chunk(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sidecar = self.require_configured_sidecar(require_string(arguments, "sidecar_id"))
        chunk_id = require_positive_int(arguments, "chunk_id")

        with connect_readonly_index(
            index_path_for_sidecar(self.registry, sidecar.id)
        ) as connection:
            row = connection.execute(
                """
                SELECT id, relative_path, start_line, end_line, text, content_hash
                FROM chunks
                WHERE id = ?
                """,
                (chunk_id,),
            ).fetchone()

        if row is None:
            raise McpError(f"chunk not found: {chunk_id}")

        return {
            "sidecar_id": sidecar.id,
            "chunk_id": int(row["id"]),
            "relative_path": str(row["relative_path"]),
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "text": str(row["text"]),
            "content_hash": str(row["content_hash"]),
        }

    def tool_read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sidecar = self.require_configured_sidecar(require_string(arguments, "sidecar_id"))
        relative_path = require_string(arguments, "path")
        path = resolve_root_relative_path(sidecar.root_path, relative_path)
        if not path.is_file():
            raise McpError(f"file not found: {relative_path}")
        if path.stat().st_size > sidecar.config.max_file_size_bytes:
            raise McpError(f"file exceeds max readable size: {relative_path}")

        text = read_text_content(path)
        return {
            "sidecar_id": sidecar.id,
            "relative_path": path.relative_to(sidecar.root_path).as_posix(),
            "text": text,
            "preview": text_preview(text),
        }

    def tool_list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sidecar = self.require_configured_sidecar(require_string(arguments, "sidecar_id"))
        prefix = optional_string(arguments, "prefix", default="")
        if prefix:
            resolve_root_relative_path(sidecar.root_path, prefix)

        files = []
        for candidate in scan_files(
            sidecar.root_path,
            max_file_size_bytes=sidecar.config.max_file_size_bytes,
        ):
            relative_path = candidate.relative_path.as_posix()
            if prefix and not relative_path.startswith(Path(prefix).as_posix().rstrip("/") + "/"):
                if relative_path != Path(prefix).as_posix():
                    continue
            files.append(
                {
                    "relative_path": relative_path,
                    "extension": candidate.extension,
                    "size_bytes": candidate.size_bytes,
                }
            )

        return {"sidecar_id": sidecar.id, "files": files}

    def tool_get_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target_sidecar_ids = self.target_sidecar_ids(arguments)
        statuses = []
        for sidecar_id in target_sidecar_ids:
            sidecar = self.registry.get(sidecar_id)
            try:
                with connect_readonly_index(
                    index_path_for_sidecar(self.registry, sidecar.id)
                ) as connection:
                    errors = readonly_list_indexing_errors(connection)
            except sqlite3.OperationalError:
                errors = []
            statuses.append(
                {
                    "sidecar": sidecar_to_dict(sidecar),
                    "errors": [indexing_error_to_dict(error) for error in errors],
                }
            )
        return {"sidecars": statuses}

    def target_sidecar_ids(self, arguments: dict[str, Any]) -> tuple[str, ...]:
        sidecar_id = optional_string(arguments, "sidecar_id", default="")
        if sidecar_id:
            self.require_configured_sidecar(sidecar_id)
            return (sidecar_id,)
        return self.sidecar_ids

    def require_configured_sidecar(self, sidecar_id: str) -> Sidecar:
        if sidecar_id not in self.sidecar_ids:
            raise McpError(f"sidecar is not configured for this MCP server: {sidecar_id}")
        return self.registry.get(sidecar_id)


def run_mcp_server(registry: SidecarRegistry, sidecar_ids: Iterable[str]) -> None:
    PsydecarMcpServer(registry, sidecar_ids).serve()


def readonly_search_sidecar(
    registry: SidecarRegistry,
    sidecar_id: str,
    query: str,
    mode: str,
    limit: int,
) -> list[SearchResult]:
    sidecar = registry.get(sidecar_id)
    try:
        with connect_readonly_index(index_path_for_sidecar(registry, sidecar.id)) as connection:
            return readonly_search_index(
                connection,
                query,
                storage_dir=registry.storage_dir(sidecar.id),
                mode=mode,
                limit=limit,
            )
    except sqlite3.OperationalError as exc:
        if "unable to open database file" in str(exc):
            return []
        raise


def readonly_search_index(
    connection: sqlite3.Connection,
    query: str,
    *,
    storage_dir: Path,
    mode: str,
    limit: int,
) -> list[SearchResult]:
    normalized_limit = normalize_limit(limit)
    if not query.strip():
        return []
    if mode == "keyword":
        return readonly_keyword_search(connection, query, limit=normalized_limit)
    if mode == "semantic":
        return readonly_semantic_search(
            connection,
            query,
            storage_dir=storage_dir,
            limit=normalized_limit,
        )
    if mode == "hybrid":
        candidate_limit = max(normalized_limit * 2, normalized_limit)
        return merge_search_results(
            readonly_keyword_search(connection, query, limit=candidate_limit),
            readonly_semantic_search(
                connection,
                query,
                storage_dir=storage_dir,
                limit=candidate_limit,
            ),
            limit=normalized_limit,
        )
    raise McpError(f"unsupported search mode: {mode}")


def readonly_keyword_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
) -> list[SearchResult]:
    match_query = build_fts_query(query)
    if not match_query:
        return []
    try:
        rows = connection.execute(
            """
            SELECT
                chunks.id AS chunk_id,
                chunks.relative_path,
                chunks.start_line,
                chunks.end_line,
                chunks.text,
                bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise

    return [
        SearchResult(
            chunk_id=int(row["chunk_id"]),
            relative_path=str(row["relative_path"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            preview=text_preview(str(row["text"])),
            score=float(-row["rank"]),
        )
        for row in rows
    ]


def readonly_semantic_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    storage_dir: Path,
    limit: int,
) -> list[SearchResult]:
    from psydecar.vectors import search_vectors, vector_runtime_available

    if not vector_runtime_available():
        return []
    return search_vectors(
        query,
        chunks=list_vector_chunks(connection),
        storage_dir=storage_dir,
        limit=limit,
    )


def readonly_list_indexing_errors(connection: sqlite3.Connection) -> list[IndexingErrorRecord]:
    try:
        rows = connection.execute(
            """
            SELECT relative_path, stage, message
            FROM indexing_errors
            ORDER BY relative_path, id
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise

    return [
        IndexingErrorRecord(
            relative_path=str(row["relative_path"]),
            stage=str(row["stage"]),
            message=str(row["message"]),
        )
        for row in rows
    ]


@contextmanager
def connect_readonly_index(index_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def parse_sidecar_ids(value: str) -> list[str]:
    sidecar_ids = [item.strip() for item in value.split(",") if item.strip()]
    if not sidecar_ids:
        raise McpError("--sidecars must include at least one sidecar id")
    return sidecar_ids


def require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpError(f"{key} must be a non-empty string")
    return value


def optional_string(arguments: dict[str, Any], key: str, *, default: str) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise McpError(f"{key} must be a string")
    return value


def require_positive_int(arguments: dict[str, Any], key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or value < 1:
        raise McpError(f"{key} must be a positive integer")
    return value


def optional_positive_int(arguments: dict[str, Any], key: str, *, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int) or value < 1:
        raise McpError(f"{key} must be a positive integer")
    return value


def jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def sidecar_to_dict(sidecar: Sidecar) -> dict[str, Any]:
    return sidecar.to_json_dict()


def search_result_to_dict(result: SearchResult) -> dict[str, Any]:
    return asdict(result)


def indexing_error_to_dict(error: IndexingErrorRecord) -> dict[str, Any]:
    return asdict(error)


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "title": "Search sidecars",
        "description": "Search one configured sidecar or all configured sidecars.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "sidecar_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["keyword", "semantic", "hybrid"]},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_chunk",
        "title": "Read chunk",
        "description": "Read an indexed chunk by sidecar id and chunk id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sidecar_id": {"type": "string"},
                "chunk_id": {"type": "integer", "minimum": 1},
            },
            "required": ["sidecar_id", "chunk_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_file",
        "title": "Read file",
        "description": "Read a text file beneath a configured sidecar source root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sidecar_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["sidecar_id", "path"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_files",
        "title": "List files",
        "description": "List indexable files beneath a configured sidecar source root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sidecar_id": {"type": "string"},
                "prefix": {"type": "string"},
            },
            "required": ["sidecar_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_status",
        "title": "Get status",
        "description": "Return indexing status and stored indexing errors.",
        "inputSchema": {
            "type": "object",
            "properties": {"sidecar_id": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": True},
    },
]
