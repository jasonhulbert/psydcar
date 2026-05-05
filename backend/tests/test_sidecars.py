import json
from datetime import UTC, datetime

import pytest

from psydecar.sidecars import (
    DEFAULT_INDEXING_STATUS,
    SIDECAR_METADATA_FILENAME,
    SidecarAlreadyExistsError,
    SidecarError,
    SidecarNotFoundError,
    SidecarRegistry,
)


def test_create_sidecar_writes_expected_storage_layout(tmp_path):
    storage_root = tmp_path / "storage"
    source_root = tmp_path / "external-source"
    source_root.mkdir()

    registry = SidecarRegistry(storage_root)
    sidecar = registry.create(source_root, name="Docs", sidecar_id="docs")

    sidecar_dir = storage_root / "sidecars" / "docs"
    metadata_path = sidecar_dir / SIDECAR_METADATA_FILENAME

    assert sidecar_dir.is_dir()
    assert metadata_path.is_file()
    assert sidecar.id == "docs"
    assert sidecar.name == "Docs"
    assert sidecar.root_path == source_root.resolve()

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["id"] == "docs"
    assert payload["name"] == "Docs"
    assert payload["root_path"] == str(source_root.resolve())
    assert payload["indexing_status"] == DEFAULT_INDEXING_STATUS
    assert payload["config"]["max_file_size_bytes"] == 1_000_000
    assert "node_modules" in payload["config"]["ignored_directories"]
    assert payload["config"]["embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert "created_at" in payload
    assert "updated_at" in payload


def test_create_sidecar_can_target_directory_outside_storage_root(tmp_path):
    storage_root = tmp_path / "storage"
    source_root = tmp_path / "project"
    source_root.mkdir()

    sidecar = SidecarRegistry(storage_root).create(source_root, sidecar_id="project")

    assert sidecar.root_path == source_root.resolve()
    assert str(sidecar.root_path).startswith(str(storage_root)) is False


def test_listing_sidecars_reads_persisted_metadata(tmp_path):
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    source_a.mkdir()
    source_b.mkdir()
    registry = SidecarRegistry(tmp_path / "storage")

    registry.create(source_b, name="Source B", sidecar_id="b")
    registry.create(source_a, name="Source A", sidecar_id="a")

    sidecars = registry.list()

    assert [sidecar.id for sidecar in sidecars] == ["a", "b"]
    assert [sidecar.name for sidecar in sidecars] == ["Source A", "Source B"]
    assert [sidecar.root_path for sidecar in sidecars] == [
        source_a.resolve(),
        source_b.resolve(),
    ]


def test_mark_indexing_preserves_existing_counts_and_last_refresh(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="source")
    last_refresh_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    registry.update_indexing_status(
        "source",
        indexing_status="indexed",
        indexed_file_count=3,
        chunk_count=9,
        error_count=1,
        last_refresh_at=last_refresh_at,
    )

    sidecar = registry.mark_indexing("source")

    assert sidecar.indexing_status == "indexing"
    assert sidecar.indexed_file_count == 3
    assert sidecar.chunk_count == 9
    assert sidecar.error_count == 1
    assert sidecar.last_refresh_at == last_refresh_at


def test_create_sidecar_rejects_duplicate_id(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="source")

    with pytest.raises(SidecarAlreadyExistsError):
        registry.create(source_root, sidecar_id="source")


def test_delete_sidecar_removes_app_managed_storage_only(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "keep.md").write_text("# keep\n", encoding="utf-8")
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="source")

    registry.delete("source")

    assert not (tmp_path / "storage" / "sidecars" / "source").exists()
    assert (source_root / "keep.md").is_file()


def test_delete_unknown_sidecar_raises(tmp_path):
    with pytest.raises(SidecarNotFoundError):
        SidecarRegistry(tmp_path / "storage").delete("missing")


def test_corrupt_sidecar_metadata_reports_storage_path(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="source")
    metadata_path = tmp_path / "storage" / "sidecars" / "source" / SIDECAR_METADATA_FILENAME
    metadata_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(SidecarError, match="corrupt sidecar metadata"):
        registry.get("source")
