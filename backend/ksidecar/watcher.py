"""Watchdog-backed sidecar file watching with debounced refreshes."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock, Timer
from typing import Any

from ksidecar.index import (
    IndexingErrorRecord,
    RefreshResult,
    record_sidecar_indexing_error,
    refresh_sidecar_index,
)
from ksidecar.sidecars import SidecarRegistry

DEFAULT_WATCH_DEBOUNCE_SECONDS = 1.5
WATCH_ERROR_PATH = "__watch__"


class WatcherError(RuntimeError):
    """Raised when file watching cannot be started or managed."""


@dataclass(frozen=True)
class WatchStatus:
    sidecar_id: str
    active: bool
    debounce_seconds: float
    pending_path_count: int
    last_event_at: datetime | None = None
    last_refresh_at: datetime | None = None
    last_batch_size: int = 0
    refresh_count: int = 0
    last_error: str | None = None


class SidecarWatchService:
    """Manage watchdog observers and debounce refreshes per sidecar."""

    def __init__(
        self,
        registry: SidecarRegistry,
        *,
        debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS,
        refresh: Callable[[SidecarRegistry, str], RefreshResult] = refresh_sidecar_index,
    ) -> None:
        if debounce_seconds <= 0:
            raise ValueError("debounce_seconds must be positive")
        self.registry = registry
        self.debounce_seconds = debounce_seconds
        self._refresh = refresh
        self._lock = RLock()
        self._observers: dict[str, Any] = {}
        self._timers: dict[str, Timer] = {}
        self._pending_paths: dict[str, set[str]] = defaultdict(set)
        self._statuses: dict[str, WatchStatus] = {}

    def start(self, sidecar_id: str) -> WatchStatus:
        sidecar = self.registry.get(sidecar_id)
        with self._lock:
            if sidecar.id in self._observers:
                return self.status(sidecar.id)

        try:
            observer = self._create_observer(sidecar.id)
            observer.schedule(
                _WatchdogEventHandler(self, sidecar.id),
                str(sidecar.root_path),
                recursive=True,
            )
            observer.start()
        except Exception as exc:  # noqa: BLE001 - watcher failures must be captured.
            self._record_failure(sidecar.id, f"failed to start watcher: {exc}")
            raise WatcherError(f"failed to start watcher for {sidecar.id}: {exc}") from exc

        with self._lock:
            self._observers[sidecar.id] = observer
            self._set_status(
                sidecar.id,
                active=True,
                last_error=None,
                update_last_error=True,
            )
            return self._statuses[sidecar.id]

    def start_all(self) -> list[WatchStatus]:
        return [self.start(sidecar.id) for sidecar in self.registry.list()]

    def stop(self, sidecar_id: str) -> WatchStatus:
        self.registry.get(sidecar_id)
        with self._lock:
            observer = self._observers.pop(sidecar_id, None)
            timer = self._timers.pop(sidecar_id, None)
            if timer:
                timer.cancel()

        if observer is not None:
            observer.stop()
            observer.join(timeout=5)

        with self._lock:
            self._set_status(sidecar_id, active=False)
            return self._statuses[sidecar_id]

    def stop_all(self) -> None:
        with self._lock:
            sidecar_ids = set(self._observers) | set(self._timers)
        for sidecar_id in sorted(sidecar_ids):
            self.stop(sidecar_id)

    def notify_change(self, sidecar_id: str, path: Path | str) -> WatchStatus:
        self.registry.get(sidecar_id)
        path_key = self._path_key(sidecar_id, Path(path))
        with self._lock:
            self._pending_paths[sidecar_id].add(path_key)
            self._set_status(
                sidecar_id,
                active=sidecar_id in self._observers,
                pending_path_count=len(self._pending_paths[sidecar_id]),
                last_event_at=datetime.now(UTC),
            )
            existing_timer = self._timers.pop(sidecar_id, None)
            if existing_timer:
                existing_timer.cancel()
            timer = Timer(self.debounce_seconds, self.flush, args=(sidecar_id,))
            timer.daemon = True
            self._timers[sidecar_id] = timer
            timer.start()
            return self._statuses[sidecar_id]

    def flush(self, sidecar_id: str) -> WatchStatus:
        self.registry.get(sidecar_id)
        with self._lock:
            changed_paths = self._pending_paths.pop(sidecar_id, set())
            self._timers.pop(sidecar_id, None)
            active = sidecar_id in self._observers
            if not changed_paths:
                self._set_status(sidecar_id, active=active, pending_path_count=0)
                return self._statuses[sidecar_id]

        try:
            self._refresh(self.registry, sidecar_id)
        except Exception as exc:  # noqa: BLE001 - watcher failures must be persisted.
            message = f"watch refresh failed for {len(changed_paths)} changed paths: {exc}"
            self._record_failure(sidecar_id, message)
            with self._lock:
                self._set_status(
                    sidecar_id,
                    active=active,
                    pending_path_count=0,
                    last_batch_size=len(changed_paths),
                    last_error=message,
                    update_last_error=True,
                )
                return self._statuses[sidecar_id]

        with self._lock:
            current = self._status_for(sidecar_id)
            self._set_status(
                sidecar_id,
                active=active,
                pending_path_count=0,
                last_refresh_at=datetime.now(UTC),
                last_batch_size=len(changed_paths),
                refresh_count=current.refresh_count + 1,
                last_error=None,
                update_last_error=True,
            )
            return self._statuses[sidecar_id]

    def status(self, sidecar_id: str) -> WatchStatus:
        self.registry.get(sidecar_id)
        with self._lock:
            self._set_status(
                sidecar_id,
                active=sidecar_id in self._observers,
                pending_path_count=len(self._pending_paths[sidecar_id]),
            )
            return self._statuses[sidecar_id]

    def statuses(self) -> list[WatchStatus]:
        sidecar_ids = {sidecar.id for sidecar in self.registry.list()}
        sidecar_ids.update(self._statuses)
        return [self.status(sidecar_id) for sidecar_id in sorted(sidecar_ids)]

    def run_until_interrupted(self, sidecar_ids: Iterable[str]) -> None:
        for sidecar_id in sidecar_ids:
            self.start(sidecar_id)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return
        finally:
            self.stop_all()

    def _create_observer(self, sidecar_id: str) -> Any:
        try:
            from watchdog.observers import Observer
        except ImportError as exc:
            raise WatcherError(
                "watchdog is required for file watching; install the ksidecar dependencies"
            ) from exc
        return Observer()

    def _path_key(self, sidecar_id: str, path: Path) -> str:
        sidecar = self.registry.get(sidecar_id)
        try:
            return path.resolve().relative_to(sidecar.root_path).as_posix()
        except (OSError, ValueError):
            return str(path)

    def _record_failure(self, sidecar_id: str, message: str) -> None:
        record_sidecar_indexing_error(
            self.registry,
            sidecar_id,
            IndexingErrorRecord(
                relative_path=WATCH_ERROR_PATH,
                stage="watch",
                message=message,
            ),
        )
        with self._lock:
            self._set_status(
                sidecar_id,
                active=sidecar_id in self._observers,
                last_error=message,
                update_last_error=True,
            )

    def _status_for(self, sidecar_id: str) -> WatchStatus:
        return self._statuses.get(
            sidecar_id,
            WatchStatus(
                sidecar_id=sidecar_id,
                active=False,
                debounce_seconds=self.debounce_seconds,
                pending_path_count=0,
            ),
        )

    def _set_status(
        self,
        sidecar_id: str,
        *,
        active: bool | None = None,
        pending_path_count: int | None = None,
        last_event_at: datetime | None = None,
        last_refresh_at: datetime | None = None,
        last_batch_size: int | None = None,
        refresh_count: int | None = None,
        last_error: str | None = None,
        update_last_error: bool = False,
    ) -> None:
        current = self._status_for(sidecar_id)
        self._statuses[sidecar_id] = WatchStatus(
            sidecar_id=sidecar_id,
            active=current.active if active is None else active,
            debounce_seconds=self.debounce_seconds,
            pending_path_count=(
                current.pending_path_count if pending_path_count is None else pending_path_count
            ),
            last_event_at=current.last_event_at if last_event_at is None else last_event_at,
            last_refresh_at=(
                current.last_refresh_at if last_refresh_at is None else last_refresh_at
            ),
            last_batch_size=(
                current.last_batch_size if last_batch_size is None else last_batch_size
            ),
            refresh_count=current.refresh_count if refresh_count is None else refresh_count,
            last_error=current.last_error if not update_last_error else last_error,
        )


class _WatchdogEventHandler:
    def __init__(self, service: SidecarWatchService, sidecar_id: str) -> None:
        self.service = service
        self.sidecar_id = sidecar_id

    def dispatch(self, event: Any) -> None:
        if getattr(event, "is_directory", False):
            return
        paths = [getattr(event, "src_path", None), getattr(event, "dest_path", None)]
        for path in paths:
            if path:
                self.service.notify_change(self.sidecar_id, Path(path))
