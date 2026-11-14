"""Which sidecars an agent connection may read, and where it may propose edits.

The MCP server, the CLI, and the dashboard are transports. They call these
rules and keep no copy of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .registry import SidecarStore, StoreError


MAX_AGENT_NAME_LENGTH = 64


@dataclass(frozen=True)
class AgentAccess:
    """One connection's fixed selection, proposal grants, and agent name."""

    selected: tuple[str, ...]
    grants: tuple[str, ...]
    agent_name: str

    def require_grant(self, sidecar_id: str) -> None:
        if sidecar_id not in self.grants:
            raise StoreError("This connection does not have proposal access to that sidecar.")


def select(store: SidecarStore, references: list[str]) -> tuple[str, ...]:
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


def grant(selected: tuple[str, ...], requested: list[str]) -> tuple[str, ...]:
    """Limit proposal grants to sidecars the connection may also read."""
    if not set(requested).issubset(selected):
        raise StoreError("Each proposal sidecar must also be selected for reading with --sidecar-id.")
    return tuple(sidecar_id for sidecar_id in selected if sidecar_id in requested)


def connect_agent(store: SidecarStore, references: list[str], *,
                  proposal_ids: list[str] | None = None, agent_name: str | None = None) -> AgentAccess:
    """Fix one connection's access. A connection with proposal grants must name its agent."""
    selected = select(store, references)
    grants = grant(selected, proposal_ids or [])
    label = (agent_name or "").strip()
    if grants and (not label or len(label) > MAX_AGENT_NAME_LENGTH or not label.isprintable()):
        raise StoreError("Use --agent-name with a display name of up to 64 characters when granting proposals.")
    return AgentAccess(selected, grants, label)
