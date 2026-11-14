"""MCP tools for one explicitly selected set of local sidecars."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from .access import AgentAccess
from .evidence import EvidenceStore
from .proposal_files import MAX_PATH_LENGTH, ProposalFiles
from .proposals import MAX_SUMMARY_LENGTH, Proposals
from .registry import SidecarStore, StoreError
from .source_scope import MAX_FILE_BYTES


def create_server(store: SidecarStore, access: AgentAccess):
    """Build a stdio server whose tools close over the connection's fixed access."""
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise StoreError("MCP support requires the mcp package. Install Psydcar with its dependencies.") from exc

    server = MCPServer("psydcar")

    @server.tool(name="search", description="Search evidence in the sidecars selected for this connection.", structured_output=True)
    def search(query: str, limit: int = 10) -> dict[str, Any]:
        """Return traceable indexed evidence and any search fallback warning."""
        evidence = EvidenceStore(store)
        results = evidence.search(list(access.selected), query, limit, exact_ids=True)
        return {"results": results, "warning": evidence.last_search_warning}

    @server.tool(
        name="retrieve", description="Read current source for an evidence ID in this connection's selected sidecars.", structured_output=True
    )
    def retrieve(evidence_id: str) -> dict[str, Any]:
        """Return source-checked evidence with its location and freshness."""
        return EvidenceStore(store).retrieve(list(access.selected), evidence_id, exact_ids=True)

    if access.grants:
        from mcp.server.mcpserver.exceptions import ToolError

        sources = ProposalFiles(store)
        proposals = Proposals(store)

        def require_grant(sidecar_id: str) -> None:
            try:
                access.require_grant(sidecar_id)
            except StoreError as exc:
                raise ToolError(str(exc)) from exc

        @server.tool(name="read_editable_file", structured_output=True)
        def read_editable_file(
            sidecar_id: str,
            path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH)],
        ) -> dict[str, Any]:
            """Read the full text and version of an existing file in a sidecar granted for proposals."""
            require_grant(sidecar_id)
            try:
                file = sources.files(sidecar_id).read(path)
            except StoreError as exc:
                raise ToolError(str(exc)) from exc
            return {key: file[key] for key in ("path", "content", "version")}

        @server.tool(
            name="propose_edit",
            structured_output=True,
            description=(
                "Queue complete replacement text and a one-line reason for the owner to review. "
                "Use the version from read_editable_file. This tool does not write the file. "
                "If the owner approves, Psydcar writes it, and the reason may become the subject of a git commit."
            ),
        )
        def propose_edit(
            sidecar_id: str,
            path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH)],
            version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
            content: Annotated[str, Field(max_length=MAX_FILE_BYTES)],
            summary: Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)],
        ) -> dict[str, Any]:
            require_grant(sidecar_id)
            try:
                return proposals.submit(sidecar_id, path, version, content, summary, access.agent_name)
            except StoreError as exc:
                raise ToolError(str(exc)) from exc

    return server


def run_mcp(store: SidecarStore, access: AgentAccess) -> None:
    create_server(store, access).run(transport="stdio")
