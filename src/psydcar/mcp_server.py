"""MCP tools for one explicitly selected set of local sidecars."""

from __future__ import annotations

from typing import Any

from .evidence import EvidenceStore
from .store import SidecarStore, StoreError


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


def create_server(store: SidecarStore, references: list[str]):
    """Build a stdio server whose tools close over the immutable selection."""
    ids = selected_ids(store, references)
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise StoreError("MCP support requires the mcp package. Install Psydcar with its dependencies.") from exc

    server = MCPServer("psydcar")

    @server.tool(name="search", description="Search evidence in the sidecars selected for this connection.", structured_output=True)
    def search(query: str, limit: int = 10) -> dict[str, Any]:
        """Return traceable indexed evidence and any search fallback warning."""
        evidence = EvidenceStore(store)
        results = evidence.search(list(ids), query, limit)
        return {"results": results, "warning": evidence.last_search_warning}

    @server.tool(name="retrieve", description="Read current source for an evidence ID in this connection's selected sidecars.", structured_output=True)
    def retrieve(evidence_id: str) -> dict[str, Any]:
        """Return source-checked evidence with its location and freshness."""
        return EvidenceStore(store).retrieve(list(ids), evidence_id)

    return server


def run_mcp(store: SidecarStore, references: list[str]) -> None:
    create_server(store, references).run(transport="stdio")
