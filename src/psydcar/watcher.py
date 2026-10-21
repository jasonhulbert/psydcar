"""Foreground source following and restart reconciliation for local sidecars."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .evidence import EvidenceStore, IndexReport, source_snapshot
from .store import SidecarStore, StoreError


@dataclass(frozen=True)
class WatchEvent:
    sidecar_id: str | None
    sidecar_name: str | None
    reason: str
    report: IndexReport | None = None
    error: str | None = None


class SidecarWatcher:
    """Poll the manual updater's source scope and reconcile missed changes."""

    def __init__(
        self,
        sidecars: SidecarStore | None = None,
        interval: float = 1.0,
        reconcile_interval: float = 60.0,
        update_lock: threading.Lock | None = None,
    ) -> None:
        if not 0.05 <= interval <= 3600:
            raise StoreError("Watch interval must be between 0.05 and 3600 seconds.")
        if not interval <= reconcile_interval <= 86400:
            raise StoreError("Full reconciliation interval must be at least the watch interval and at most 86400 seconds.")
        self.sidecars = sidecars or SidecarStore()
        self.evidence = EvidenceStore(self.sidecars)
        self.interval = interval
        self.reconcile_interval = reconcile_interval
        self.update_lock = update_lock
        self._snapshots: dict[str, tuple] = {}
        self._snapshot_lock = threading.Lock()
        self._last_reconcile = time.monotonic()
        self._started = False

    def step(self, force: bool = False) -> list[WatchEvent]:
        """Run one check. Call repeatedly from a service or use run()."""
        now = time.monotonic()
        startup = not self._started
        full = force or startup or now - self._last_reconcile >= self.reconcile_interval
        events = []
        sidecars = self.sidecars.list()
        active_ids = {sidecar.id for sidecar in sidecars}
        with self._snapshot_lock:
            for retired in self._snapshots.keys() - active_ids:
                del self._snapshots[retired]
        for sidecar in sidecars:
            snapshot = source_snapshot(sidecar)
            with self._snapshot_lock:
                previous = self._snapshots.get(sidecar.id)
            if previous is not None and snapshot == previous and not full:
                continue
            reason = (
                "startup" if startup else "new_sidecar" if previous is None else
                "source_changed" if snapshot != previous else "periodic"
            )
            try:
                if self.update_lock is None:
                    report = self.evidence.update(sidecar.id)
                else:
                    with self.update_lock:
                        report = self.evidence.update(sidecar.id)
            except StoreError as exc:
                events.append(WatchEvent(sidecar.id, sidecar.name, reason, error=str(exc)))
            else:
                events.append(WatchEvent(sidecar.id, sidecar.name, reason, report=report))
            # Keep the pre-update fingerprint. A change during processing then
            # differs on the next pass and receives another complete update.
            with self._snapshot_lock:
                self._snapshots[sidecar.id] = snapshot
        self._started = True
        if full:
            self._last_reconcile = time.monotonic()
        return events

    def source_changed(self, sidecar) -> bool:
        """Report scoped source drift between watcher passes."""
        with self._snapshot_lock:
            previous = self._snapshots.get(sidecar.id)
        return previous is not None and source_snapshot(sidecar) != previous

    def run(
        self,
        stop: threading.Event | None = None,
        on_event: Callable[[WatchEvent], None] | None = None,
    ) -> None:
        """Follow changes until stopped; a new instance reconciles on startup."""
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                events = self.step()
            except StoreError as exc:
                events = [WatchEvent(None, None, "watch_error", error=str(exc))]
            if on_event is not None:
                for event in events:
                    on_event(event)
            stop.wait(self.interval)
