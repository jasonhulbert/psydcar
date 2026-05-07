"""Root-confined filesystem helpers for sidecar source directories."""

from __future__ import annotations

from pathlib import Path


class PathSafetyError(ValueError):
    """Raised when a requested sidecar path escapes its configured root."""


def resolve_source_root(root: Path | str) -> Path:
    """Resolve and validate a sidecar source root."""

    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"sidecar source root does not exist: {resolved}")
    return resolved


def resolve_root_relative_path(root: Path | str, relative_path: Path | str) -> Path:
    """Resolve ``relative_path`` under ``root`` and reject traversal escapes.

    The root is the user-selected sidecar source directory, not app-managed
    storage under ``~/.psydcar``.
    """

    source_root = resolve_source_root(root)
    requested = Path(relative_path)
    if requested.is_absolute():
        raise PathSafetyError("sidecar paths must be relative to the source root")

    resolved = (source_root / requested).resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError as exc:
        raise PathSafetyError(f"path escapes sidecar source root: {relative_path}") from exc
    return resolved


def path_is_within_root(root: Path | str, path: Path | str) -> bool:
    """Return whether ``path`` resolves inside ``root``."""

    source_root = resolve_source_root(root)
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError:
        return False
    return True
