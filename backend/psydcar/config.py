"""Application configuration primitives."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from psydcar.defaults import DEFAULT_EMBEDDING_MODEL
from psydcar.paths import app_storage_root
from psydcar.scanner import DEFAULT_MAX_FILE_SIZE_BYTES, IGNORED_DIRECTORY_NAMES

MAX_FILE_SIZE_ENV = "PSYDCAR_MAX_FILE_SIZE_BYTES"
IGNORED_DIRS_ENV = "PSYDCAR_IGNORED_DIRS"
EMBEDDING_MODEL_ENV = "PSYDCAR_EMBEDDING_MODEL"


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration shared by CLI, API, and future services."""

    storage_root: Path = field(default_factory=app_storage_root)
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    ignored_directories: tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(IGNORED_DIRECTORY_NAMES))
    )
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    @classmethod
    def load(cls) -> AppConfig:
        """Load configuration from the current process environment."""

        return cls(
            storage_root=app_storage_root(),
            max_file_size_bytes=load_positive_int_env(
                MAX_FILE_SIZE_ENV,
                DEFAULT_MAX_FILE_SIZE_BYTES,
            ),
            ignored_directories=load_csv_env(
                IGNORED_DIRS_ENV,
                tuple(sorted(IGNORED_DIRECTORY_NAMES)),
            ),
            embedding_model=os.environ.get(EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL),
        )


def load_positive_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def load_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    entries = tuple(entry.strip() for entry in value.split(",") if entry.strip())
    return entries or default
