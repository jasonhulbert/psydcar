import time

from psydcar.index import RefreshResult, list_sidecar_indexing_errors
from psydcar.sidecars import SidecarRegistry
from psydcar.watcher import SidecarWatchService


def test_watcher_batches_burst_changes_into_one_refresh(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="docs")
    calls: list[str] = []

    def refresh(registry: SidecarRegistry, sidecar_id: str):
        calls.append(sidecar_id)
        return RefreshResult(
            document_count=0,
            chunk_count=0,
            error_count=0,
            new_count=0,
            modified_count=0,
            unchanged_count=0,
            deleted_count=0,
        )

    service = SidecarWatchService(registry, debounce_seconds=0.02, refresh=refresh)

    service.notify_change("docs", source_root / "a.md")
    service.notify_change("docs", source_root / "b.md")
    time.sleep(0.08)

    status = service.status("docs")
    assert calls == ["docs"]
    assert status.pending_path_count == 0
    assert status.last_batch_size == 2
    assert status.refresh_count == 1
    assert status.last_error is None


def test_watcher_refresh_failure_is_recorded_as_sidecar_error(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="docs")

    def refresh(registry: SidecarRegistry, sidecar_id: str):
        raise RuntimeError("refresh exploded")

    service = SidecarWatchService(registry, debounce_seconds=0.02, refresh=refresh)

    service.notify_change("docs", source_root / "a.md")
    time.sleep(0.08)

    status = service.status("docs")
    errors = list_sidecar_indexing_errors(registry, "docs")
    sidecar = registry.get("docs")

    assert status.last_error is not None
    assert "refresh exploded" in status.last_error
    assert [(error.relative_path, error.stage) for error in errors] == [("__watch__", "watch")]
    assert "refresh exploded" in errors[0].message
    assert sidecar.indexing_status == "indexed_with_errors"
    assert sidecar.error_count == 1
