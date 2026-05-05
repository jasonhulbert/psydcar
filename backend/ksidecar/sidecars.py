"""Sidecar metadata models and registry operations."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from ksidecar.defaults import DEFAULT_EMBEDDING_MODEL
from ksidecar.filesystem import resolve_source_root
from ksidecar.paths import ensure_app_storage_root, sidecars_root
from ksidecar.scanner import DEFAULT_MAX_FILE_SIZE_BYTES, IGNORED_DIRECTORY_NAMES

SIDECAR_METADATA_FILENAME = "sidecar.json"
DEFAULT_INDEXING_STATUS = "not_indexed"


class SidecarError(RuntimeError):
    """Base exception for sidecar registry failures."""


class SidecarAlreadyExistsError(SidecarError):
    """Raised when a sidecar id already exists in the registry."""


class SidecarNotFoundError(SidecarError):
    """Raised when a requested sidecar id is not present."""


@dataclass(frozen=True)
class SidecarConfig:
    """Per-sidecar indexing configuration."""

    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    ignored_directories: tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(IGNORED_DIRECTORY_NAMES))
    )
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    def __post_init__(self) -> None:
        if self.max_file_size_bytes <= 0:
            raise SidecarError("max_file_size_bytes must be positive")
        object.__setattr__(
            self,
            "ignored_directories",
            tuple(str(name) for name in self.ignored_directories if str(name).strip()),
        )
        if not self.embedding_model.strip():
            raise SidecarError("embedding_model must not be empty")


@dataclass(frozen=True)
class Sidecar:
    """Persisted sidecar metadata."""

    id: str
    name: str
    root_path: Path
    created_at: datetime
    updated_at: datetime
    indexing_status: str = DEFAULT_INDEXING_STATUS
    last_refresh_at: datetime | None = None
    indexed_file_count: int = 0
    chunk_count: int = 0
    error_count: int = 0
    config: SidecarConfig = field(default_factory=SidecarConfig)

    @classmethod
    def create(
        cls,
        *,
        root_path: Path,
        name: str | None = None,
        sidecar_id: str | None = None,
        config: SidecarConfig | None = None,
    ) -> Sidecar:
        now = utc_now()
        resolved_root = resolve_source_root(root_path)
        return cls(
            id=sidecar_id or uuid.uuid4().hex,
            name=name or resolved_root.name,
            root_path=resolved_root,
            created_at=now,
            updated_at=now,
            config=config or SidecarConfig(),
        )

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> Sidecar:
        return cls(
            id=payload["id"],
            name=payload["name"],
            root_path=Path(payload["root_path"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
            indexing_status=payload.get("indexing_status", DEFAULT_INDEXING_STATUS),
            last_refresh_at=parse_optional_datetime(payload.get("last_refresh_at")),
            indexed_file_count=int(payload.get("indexed_file_count", 0)),
            chunk_count=int(payload.get("chunk_count", 0)),
            error_count=int(payload.get("error_count", 0)),
            config=SidecarConfig(**payload.get("config", {})),
        )

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["root_path"] = str(self.root_path)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        payload["last_refresh_at"] = (
            self.last_refresh_at.isoformat() if self.last_refresh_at else None
        )
        return payload


class SidecarRegistry:
    """Persist and discover sidecars under app-managed storage."""

    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = storage_root

    @property
    def root(self) -> Path:
        return ensure_app_storage_root(self.storage_root)

    @property
    def sidecars_root(self) -> Path:
        return sidecars_root(self.root)

    def create(
        self,
        source_root: Path | str,
        *,
        name: str | None = None,
        sidecar_id: str | None = None,
        config: SidecarConfig | None = None,
    ) -> Sidecar:
        sidecar = Sidecar.create(
            root_path=Path(source_root),
            name=name,
            sidecar_id=sidecar_id,
            config=config,
        )
        sidecar_dir = self.storage_dir(sidecar.id)
        if sidecar_dir.exists():
            raise SidecarAlreadyExistsError(f"sidecar already exists: {sidecar.id}")

        sidecar_dir.mkdir(parents=False)
        self._write_metadata(sidecar)
        return sidecar

    def list(self) -> list[Sidecar]:
        sidecars: list[Sidecar] = []
        for sidecar_dir in sorted(self.sidecars_root.iterdir()):
            metadata_path = sidecar_dir / SIDECAR_METADATA_FILENAME
            if sidecar_dir.is_dir() and metadata_path.is_file():
                sidecars.append(self._read_metadata(metadata_path))
        return sidecars

    def get(self, sidecar_id: str) -> Sidecar:
        metadata_path = self.metadata_path(sidecar_id)
        if not metadata_path.is_file():
            raise SidecarNotFoundError(f"sidecar not found: {sidecar_id}")
        return self._read_metadata(metadata_path)

    def delete(self, sidecar_id: str) -> None:
        sidecar_dir = self.storage_dir(sidecar_id)
        if not sidecar_dir.is_dir():
            raise SidecarNotFoundError(f"sidecar not found: {sidecar_id}")
        shutil.rmtree(sidecar_dir)

    def update_indexing_status(
        self,
        sidecar_id: str,
        *,
        indexing_status: str,
        indexed_file_count: int,
        chunk_count: int,
        error_count: int,
        last_refresh_at: datetime | None = None,
    ) -> Sidecar:
        sidecar = self.get(sidecar_id)
        now = utc_now()
        updated = replace(
            sidecar,
            updated_at=now,
            indexing_status=indexing_status,
            last_refresh_at=last_refresh_at or now,
            indexed_file_count=indexed_file_count,
            chunk_count=chunk_count,
            error_count=error_count,
        )
        self._write_metadata(updated)
        return updated

    def storage_dir(self, sidecar_id: str) -> Path:
        return self.sidecars_root / sidecar_id

    def metadata_path(self, sidecar_id: str) -> Path:
        return self.storage_dir(sidecar_id) / SIDECAR_METADATA_FILENAME

    def _write_metadata(self, sidecar: Sidecar) -> None:
        metadata_path = self.metadata_path(sidecar.id)
        metadata_path.write_text(
            json.dumps(sidecar.to_json_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _read_metadata(self, metadata_path: Path) -> Sidecar:
        try:
            return Sidecar.from_json_dict(json.loads(metadata_path.read_text(encoding="utf-8")))
        except (JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SidecarError(
                f"corrupt sidecar metadata at {metadata_path}: {exc}. "
                "Delete or recreate this sidecar's app-managed storage."
            ) from exc


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
