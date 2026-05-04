"""Application configuration primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ksidecar.paths import app_storage_root


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration shared by CLI, API, and future services."""

    storage_root: Path = field(default_factory=app_storage_root)

    @classmethod
    def load(cls) -> AppConfig:
        """Load configuration from the current process environment."""

        return cls()
