"""Sidecar file access shared by the dashboard and agent proposals."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..files import SourceFiles
from ..store import SidecarStore


class Workspace:
    def __init__(self, store: SidecarStore):
        self.store = store

    def files(self, sidecar_id: str) -> SourceFiles:
        return SourceFiles(self.store.get(sidecar_id, exact_id=True))

    @contextmanager
    def edit(self, sidecar_id: str) -> Iterator[SourceFiles]:
        with self.store.file_writes():
            yield self.files(sidecar_id)
