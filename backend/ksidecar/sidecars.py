"""Sidecar metadata models and registry operations."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ksidecar.filesystem import resolve_source_root
from ksidecar.paths import ensure_app_storage_root, sidecars_root

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

    max_file_size_bytes: int = 1_000_000


@dataclass(frozen=True)
class Sidecar:
    """Persisted sidecar metadata."""

    id: str
    name: str
    root_path: Path
    created_at: datetime
    updated_at: datetime
    indexing_status: str = DEFAULT_INDEXING_STATUS
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
            config=SidecarConfig(**payload.get("config", {})),
        )

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["root_path"] = str(self.root_path)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
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
        return Sidecar.from_json_dict(json.loads(metadata_path.read_text(encoding="utf-8")))


def utc_now() -> datetime:
    return datetime.now(UTC)
