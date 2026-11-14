"""Persistent, single-user sidecar definitions shared by Psydcar interfaces."""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .storage import Tables, connect, copy_rows, exclusive_file_lock, read_only


class StoreError(Exception):
    """A sidecar operation cannot be completed as requested."""


@dataclass(frozen=True)
class Sidecar:
    id: str
    name: str
    source_path: Path
    created_at: str
    updated_at: str


def default_store_dir() -> Path:
    """Return a per-user location independent of the current project directory."""
    override = os.environ.get("PSYDCAR_HOME")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise StoreError("PSYDCAR_HOME must be an absolute directory path.")
        return path.resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "psydcar"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "psydcar"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share") / "psydcar"


def _validate_name(name: str) -> str:
    if not name or name != name.strip():
        raise StoreError("Sidecar name must be nonempty and have no leading or trailing spaces.")
    if len(name) > 120 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise StoreError("Sidecar name must be at most 120 characters and contain no control characters.")
    return name


def _validate_source(source: str | Path) -> Path:
    try:
        path = Path(source).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise StoreError(f"Source directory does not exist or cannot be resolved: {source}") from exc
    if not path.is_dir():
        raise StoreError(f"Source must be a directory: {path}")
    try:
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError as exc:
        raise StoreError(f"Source directory cannot be read: {path}: {exc.strerror or exc}") from exc
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _from_row(row: sqlite3.Row) -> Sidecar:
    return Sidecar(
        id=row["id"],
        name=row["name"],
        source_path=Path(row["source_path"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _tables(legacy_path: Path) -> Tables:
    def create(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE sidecars (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                source_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        # Version 0.3.0 kept everything in one database. It stays in place as the backup.
        if legacy_path.is_file():
            with read_only(legacy_path) as legacy:
                copy_rows(legacy, connection, "sidecars", "id, name, source_path, created_at, updated_at")

    return Tables("registry", "sidecars", (create,))


class SidecarStore:
    """Sidecar registration and the paths to its local state and search stores."""

    def __init__(self, db_path: Path | None = None):
        path = Path(db_path) if db_path is not None else default_store_dir() / "state.sqlite3"
        if path.name in {"state.sqlite3", "psydcar.sqlite3"}:
            self.db_path = path.with_name("state.sqlite3")
            self.index_path = path.with_name("search.sqlite3")
            self.legacy_path = path.with_name("psydcar.sqlite3")
        else:
            name = path.name.removesuffix(".state.sqlite3")
            self.db_path = path.with_name(f"{name}.state.sqlite3")
            self.index_path = path.with_name(f"{name}.search.sqlite3")
            self.legacy_path = path.with_name(name)
        self._tables = _tables(self.legacy_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            with connect(self.db_path, self._tables) as connection:
                yield connection
        except (OSError, KeyError) as exc:
            raise StoreError(f"Cannot open sidecar state at {self.db_path}: {exc}") from exc

    @contextmanager
    def file_writes(self) -> Iterator[None]:
        """Serialize version checks and file mutations across local processes."""
        try:
            with exclusive_file_lock(self.db_path.with_suffix(".files.lock")):
                yield
        except OSError as exc:
            raise StoreError(f"Cannot change sidecar files: {exc}") from exc

    def create(self, name: str, source: str | Path) -> Sidecar:
        name = _validate_name(name)
        source_path = _validate_source(source)
        sidecar_id = str(uuid.uuid4())
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO sidecars (id, name, source_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (sidecar_id, name, str(source_path), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f'A sidecar named "{name}" already exists.') from exc
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot create sidecar: {exc}") from exc
        return Sidecar(sidecar_id, name, source_path, now, now)

    def list(self) -> list[Sidecar]:
        try:
            with self._connect() as connection:
                rows = connection.execute("SELECT * FROM sidecars ORDER BY name COLLATE NOCASE, id").fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot list sidecars: {exc}") from exc
        return [_from_row(row) for row in rows]

    def ids(self) -> set[str]:
        """Return the IDs of every registered sidecar."""
        try:
            with self._connect() as connection:
                return {row["id"] for row in connection.execute("SELECT id FROM sidecars")}
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot list sidecars: {exc}") from exc

    @staticmethod
    def _find(connection: sqlite3.Connection, reference: str, exact_id: bool = False) -> sqlite3.Row:
        # Prefer an exact ID if a name ever happens to match another record's ID.
        row = connection.execute("SELECT * FROM sidecars WHERE id = ?", (reference,)).fetchone()
        if row is None and not exact_id:
            row = connection.execute("SELECT * FROM sidecars WHERE name = ? COLLATE NOCASE", (reference,)).fetchone()
        if row is None:
            raise StoreError(f'No sidecar named or identified by "{reference}".')
        return row

    def get(self, reference: str, *, exact_id: bool = False) -> Sidecar:
        try:
            with self._connect() as connection:
                return _from_row(self._find(connection, reference, exact_id))
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot inspect sidecar: {exc}") from exc

    def rename(self, reference: str, new_name: str) -> Sidecar:
        new_name = _validate_name(new_name)
        try:
            with self._connect() as connection:
                row = self._find(connection, reference)
                connection.execute(
                    "UPDATE sidecars SET name = ?, updated_at = ? WHERE id = ?",
                    (new_name, _now(), row["id"]),
                )
            return self.get(row["id"], exact_id=True)
        except sqlite3.IntegrityError as exc:
            raise StoreError(f'A sidecar named "{new_name}" already exists.') from exc
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot rename sidecar: {exc}") from exc

    def remove(self, reference: str) -> Sidecar:
        """Remove the registration. Layers that keep data for a sidecar clean it up from the remaining IDs."""
        try:
            with self.file_writes(), self._connect() as connection:
                row = self._find(connection, reference)
                connection.execute("DELETE FROM sidecars WHERE id = ?", (row["id"],))
            return _from_row(row)
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot remove sidecar: {exc}") from exc
