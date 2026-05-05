"""Filesystem paths for app-managed Psydecar data."""

from __future__ import annotations

import os
from pathlib import Path

APP_HOME_ENV = "PSYDECAR_HOME"
DEFAULT_APP_DIRNAME = ".psydecar"


def app_storage_root() -> Path:
    """Return the app-managed storage root.

    The default is ``~/.psydecar``. Setting ``PSYDECAR_HOME`` is useful for tests and
    isolated local development.
    """

    configured = os.environ.get(APP_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / DEFAULT_APP_DIRNAME).resolve()


def sidecars_root(storage_root: Path | None = None) -> Path:
    """Return the directory containing persisted sidecar records."""

    root = storage_root if storage_root is not None else app_storage_root()
    return root / "sidecars"


def ensure_app_storage_root(storage_root: Path | None = None) -> Path:
    """Create and return the app-managed storage root."""

    root = storage_root if storage_root is not None else app_storage_root()
    root.mkdir(parents=True, exist_ok=True)
    sidecars_root(root).mkdir(parents=True, exist_ok=True)
    return root
