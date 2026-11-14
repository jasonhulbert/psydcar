"""SQLite helpers every layer uses to keep its own tables.

Psydcar keeps two databases. The state database holds what the owner owns. The
search database holds the rebuildable index. Each layer declares the tables it
owns in one of them as a list of upgrade steps, and opens its own connection
through ``connect``. No layer reads or writes another layer's tables.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


Step = Callable[[sqlite3.Connection], None] | tuple[str, ...]


@dataclass(frozen=True)
class Tables:
    """The tables one layer owns in one database.

    Step n moves the layer's tables from version n - 1 to version n. A step is a
    tuple of SQL statements or a function of the connection. Each step runs in
    the transaction that records the new version, so an interrupted upgrade
    repeats from the step that did not finish.

    ``first_table`` names a table the first step creates. When it already exists
    with no recorded version, an unreleased build created the first step's tables
    before versions were recorded per layer, and only the version is recorded.
    """

    owner: str
    first_table: str
    steps: tuple[Step, ...]


_VERSIONS = "CREATE TABLE IF NOT EXISTS schema_versions (owner TEXT PRIMARY KEY, version INTEGER NOT NULL)"


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


@contextmanager
def connect(path: Path, tables: Tables) -> Iterator[sqlite3.Connection]:
    """Open a database with the caller's tables present and upgraded."""
    with database(path) as connection:
        _ensure(connection, tables)
        yield connection


@contextmanager
def exclusive_file_lock(path: Path, *, wait: bool = True) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _recorded(connection: sqlite3.Connection, owner: str) -> int | None:
    try:
        row = connection.execute("SELECT version FROM schema_versions WHERE owner = ?", (owner,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row is not None else None


def _ensure(connection: sqlite3.Connection, tables: Tables) -> None:
    target = len(tables.steps)
    recorded = _recorded(connection, tables.owner)
    if recorded is not None and recorded >= target:
        return
    # Only one process may upgrade. Read the version again inside the lock.
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(_VERSIONS)
        version = _recorded(connection, tables.owner)
        if version is None:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tables.first_table,)
            ).fetchone()
            version = 1 if existing is not None else 0
        for step in tables.steps[version:]:
            if callable(step):
                step(connection)
            else:
                for statement in step:
                    connection.execute(statement)
        connection.execute(
            """INSERT INTO schema_versions (owner, version) VALUES (?, ?)
               ON CONFLICT(owner) DO UPDATE SET version = excluded.version""",
            (tables.owner, max(version, target)),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """Read a database without changing it, from one consistent snapshot."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def copy_rows(source: sqlite3.Connection, destination: sqlite3.Connection, table: str, columns: str, *,
              target: str | None = None, defaults: dict[str, str] | None = None) -> None:
    """Copy a table's rows into a table of the same shape. A missing source table copies nothing."""
    # Table names, column names, and defaults are internal schema constants.
    available = {row[1] for row in source.execute(f"PRAGMA table_info({table})")}
    if not available:
        return
    names = columns.split(", ")
    defaults = defaults or {}
    selected = ", ".join(name if name in available else defaults[name] for name in names)
    placeholders = ", ".join("?" for _ in names)
    destination.executemany(
        f"INSERT INTO {target or table} ({columns}) VALUES ({placeholders})",
        source.execute(f"SELECT {selected} FROM {table}"),
    )
