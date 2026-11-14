"""Independent SQLite stores for user state and the rebuildable search index."""

from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


_STATE_TABLES = (
    """CREATE TABLE sidecars (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL COLLATE NOCASE UNIQUE,
        source_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE embedding_provider (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        base_url TEXT NOT NULL,
        model TEXT NOT NULL,
        api_key_env TEXT,
        allow_insecure_http INTEGER NOT NULL,
        revision TEXT NOT NULL,
        configured_at TEXT NOT NULL
    )""",
    """CREATE TABLE index_cleanup (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        token TEXT NOT NULL
    )""",
)

_PROPOSALS_SCHEMA = (
    """CREATE TABLE file_proposals (
        id TEXT PRIMARY KEY,
        sidecar_id TEXT NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        summary TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'applying', 'approved', 'rejected')),
        created_at TEXT NOT NULL,
        decided_at TEXT,
        base_version TEXT NOT NULL,
        result_version TEXT NOT NULL,
        newline TEXT NOT NULL,
        bom INTEGER NOT NULL,
        before_text TEXT,
        proposed_text TEXT
    )""",
    """CREATE INDEX file_proposals_queue
       ON file_proposals(sidecar_id, status, created_at DESC, id)""",
)


_INDEX_SCHEMA = """
    CREATE TABLE IF NOT EXISTS sidecar_index (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        sidecar_id TEXT NOT NULL REFERENCES sidecar_index(id) ON DELETE CASCADE,
        relative_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        indexed_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'text',
        UNIQUE(sidecar_id, relative_path)
    );
    CREATE TABLE IF NOT EXISTS passages (
        id TEXT PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        context TEXT NOT NULL,
        page_number INTEGER
    );
    CREATE TABLE IF NOT EXISTS pdf_pages (
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL,
        status TEXT NOT NULL,
        message TEXT NOT NULL,
        PRIMARY KEY(document_id, page_number)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
        evidence_id UNINDEXED, context, tokenize='unicode61'
    );
    CREATE TRIGGER IF NOT EXISTS passages_after_delete AFTER DELETE ON passages BEGIN
        DELETE FROM passages_fts WHERE evidence_id = old.id;
    END;
    CREATE TABLE IF NOT EXISTS sidecar_updates (
        sidecar_id TEXT PRIMARY KEY REFERENCES sidecar_index(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        last_success_at TEXT,
        added INTEGER NOT NULL DEFAULT 0,
        changed INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0,
        unchanged INTEGER NOT NULL DEFAULT 0,
        issues_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS passage_embeddings (
        passage_id TEXT PRIMARY KEY REFERENCES passages(id) ON DELETE CASCADE,
        provider_revision TEXT NOT NULL,
        vector_json TEXT NOT NULL,
        dimension INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sidecar_embedding_state (
        sidecar_id TEXT PRIMARY KEY REFERENCES sidecar_index(id) ON DELETE CASCADE,
        provider_revision TEXT NOT NULL,
        status TEXT NOT NULL,
        embedded_count INTEGER NOT NULL,
        expected_count INTEGER NOT NULL,
        message TEXT,
        updated_at TEXT NOT NULL
    );
"""


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
def exclusive_file_lock(path: Path, *, wait: bool = True) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def initialize_index(connection: sqlite3.Connection) -> None:
    if _version(connection) == 0:
        connection.executescript("BEGIN IMMEDIATE;" + _INDEX_SCHEMA + "PRAGMA user_version = 1; COMMIT;")


def initialize_state(connection: sqlite3.Connection, index_path: Path, legacy_path: Path | None) -> None:
    if _version(connection) >= 2:
        return
    # Only one process may migrate. The state version commits after the index copy,
    # so a crash between commits causes the copy to be repeated from the original.
    connection.execute("BEGIN IMMEDIATE")
    version = _version(connection)
    if version == 0:
        for statement in _STATE_TABLES:
            connection.execute(statement)
        if legacy_path is not None and legacy_path.is_file():
            _migrate_legacy(connection, index_path, legacy_path)
    if version < 2:
        for statement in _PROPOSALS_SCHEMA:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 2")


def _migrate_legacy(state: sqlite3.Connection, index_path: Path, legacy_path: Path) -> None:
    legacy = sqlite3.connect(legacy_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        legacy.execute("BEGIN")
        _copy_table(legacy, state, "sidecars", "id, name, source_path, created_at, updated_at")
        _copy_table(legacy, state, "embedding_provider",
                    "id, base_url, model, api_key_env, allow_insecure_http, revision, configured_at")
        with database(index_path) as index:
            initialize_index(index)
            index.execute("BEGIN IMMEDIATE")
            index.execute("DELETE FROM sidecar_index")
            index.execute("DELETE FROM passages_fts")
            _copy_table(legacy, index, "sidecars", "id, status, updated_at", target="sidecar_index")
            _copy_table(legacy, index, "documents", "id, sidecar_id, relative_path, sha256, indexed_at, kind",
                        defaults={"kind": "'text'"})
            _copy_table(legacy, index, "passages", "id, document_id, start_line, end_line, context, page_number",
                        defaults={"page_number": "NULL"})
            _copy_table(legacy, index, "pdf_pages", "document_id, page_number, status, message")
            _copy_table(legacy, index, "sidecar_updates",
                        "sidecar_id, run_id, started_at, completed_at, last_success_at, added, changed, deleted, unchanged, issues_json")
            _copy_table(legacy, index, "passage_embeddings", "passage_id, provider_revision, vector_json, dimension")
            _copy_table(legacy, index, "sidecar_embedding_state",
                        "sidecar_id, provider_revision, status, embedded_count, expected_count, message, updated_at")
            index.execute("INSERT INTO passages_fts (evidence_id, context) SELECT id, context FROM passages")
    finally:
        legacy.close()


def _copy_table(source: sqlite3.Connection, destination: sqlite3.Connection, table: str, columns: str, *,
                target: str | None = None, defaults: dict[str, str] | None = None) -> None:
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
