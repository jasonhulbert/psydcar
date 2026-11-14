"""MCP tools for one explicitly selected set of local sidecars."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from .evidence import EvidenceStore
from .proposal_files import MAX_PATH_LENGTH, Workspace
from .proposals import MAX_SUMMARY_LENGTH, Proposals
from .registry import SidecarStore, StoreError
from .source_scope import MAX_FILE_BYTES


def selected_ids(store: SidecarStore, references: list[str]) -> tuple[str, ...]:
    """Resolve stable IDs once, before a connection can receive requests."""
    if not references:
        raise StoreError("Select at least one sidecar ID with --sidecar-id before starting MCP.")
    selected: list[str] = []
    for reference in references:
        sidecar = store.get(reference)
        if sidecar.id != reference:
            raise StoreError(f'MCP selection must use a stable sidecar ID, not a name: "{reference}".')
        if sidecar.id not in selected:
            selected.append(sidecar.id)
    return tuple(selected)


def proposal_scope(selected: tuple[str, ...], requested: list[str]) -> tuple[str, ...]:
    if not set(requested).issubset(selected):
        raise StoreError("Each proposal sidecar must also be selected for reading with --sidecar-id.")
    return tuple(sidecar_id for sidecar_id in selected if sidecar_id in requested)


def create_server(store: SidecarStore, references: list[str], *,
                  proposal_ids: list[str] | None = None, agent_name: str | None = None):
    """Build a stdio server whose tools close over the immutable selection."""
    ids = selected_ids(store, references)
    grants = proposal_scope(ids, proposal_ids or [])
    label = (agent_name or "").strip()
    if grants and (not label or len(label) > 64 or not label.isprintable()):
        raise StoreError("Use --agent-name with a display name of up to 64 characters when granting proposals.")
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise StoreError("MCP support requires the mcp package. Install Psydcar with its dependencies.") from exc

    server = MCPServer("psydcar")

    @server.tool(name="search", description="Search evidence in the sidecars selected for this connection.", structured_output=True)
    def search(query: str, limit: int = 10) -> dict[str, Any]:
        """Return traceable indexed evidence and any search fallback warning."""
        evidence = EvidenceStore(store)
        results = evidence.search(list(ids), query, limit, exact_ids=True)
        return {"results": results, "warning": evidence.last_search_warning}

    @server.tool(name="retrieve", description="Read current source for an evidence ID in this connection's selected sidecars.", structured_output=True)
    def retrieve(evidence_id: str) -> dict[str, Any]:
        """Return source-checked evidence with its location and freshness."""
        return EvidenceStore(store).retrieve(list(ids), evidence_id, exact_ids=True)

    if grants:
        from mcp.server.mcpserver.exceptions import ToolError

        workspace = Workspace(store)
        proposals = Proposals(store)

        def require_grant(sidecar_id: str) -> None:
            if sidecar_id not in grants:
                raise ToolError("This connection does not have proposal access to that sidecar.")

        @server.tool(name="read_editable_file", structured_output=True)
        def read_editable_file(
            sidecar_id: str, path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH)],
        ) -> dict[str, Any]:
            """Read the full text and version of an existing file in a sidecar granted for proposals."""
            require_grant(sidecar_id)
            try:
                file = workspace.files(sidecar_id).read(path)
            except StoreError as exc:
                raise ToolError(str(exc)) from exc
            return {key: file[key] for key in ("path", "content", "version")}

        @server.tool(name="propose_edit", structured_output=True)
        def propose_edit(
            sidecar_id: str, path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH)],
            version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
            content: Annotated[str, Field(max_length=MAX_FILE_BYTES)],
            summary: Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)],
        ) -> dict[str, Any]:
            """Queue replacement text and a one-line reason for human review. Use the version from read_editable_file. This tool does not write the file."""
            require_grant(sidecar_id)
            try:
                return proposals.submit(sidecar_id, path, version, content, summary, label)
            except StoreError as exc:
                raise ToolError(str(exc)) from exc

    return server


def run_mcp(store: SidecarStore, references: list[str], *,
            proposal_ids: list[str] | None = None, agent_name: str | None = None) -> None:
    create_server(store, references, proposal_ids=proposal_ids, agent_name=agent_name).run(transport="stdio")
