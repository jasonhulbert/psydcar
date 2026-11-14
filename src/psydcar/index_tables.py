"""The evidence index's tables and their upkeep.

The evidence index keeps two things in the state database: the embeddings
provider setting, which the owner chose, and a durable marker that outdated
embeddings need removal. Everything else it keeps in the search database,
which can be deleted at any time and is rebuilt by indexing again.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from .registry import SidecarStore, StoreError
from .storage import Tables, connect, copy_rows, read_only


_SEARCH_TABLES = Tables("evidence index", "sidecar_index", ((
    """CREATE TABLE sidecar_index (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        sidecar_id TEXT NOT NULL REFERENCES sidecar_index(id) ON DELETE CASCADE,
        relative_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        indexed_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'text',
        UNIQUE(sidecar_id, relative_path)
    )""",
    """CREATE TABLE passages (
        id TEXT PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        context TEXT NOT NULL,
        page_number INTEGER
    )""",
    """CREATE TABLE pdf_pages (
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL,
        status TEXT NOT NULL,
        message TEXT NOT NULL,
        PRIMARY KEY(document_id, page_number)
    )""",
    """CREATE VIRTUAL TABLE passages_fts USING fts5(
        evidence_id UNINDEXED, context, tokenize='unicode61'
    )""",
    """CREATE TRIGGER passages_after_delete AFTER DELETE ON passages BEGIN
        DELETE FROM passages_fts WHERE evidence_id = old.id;
    END""",
    """CREATE TABLE sidecar_updates (
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
    )""",
    """CREATE TABLE passage_embeddings (
        passage_id TEXT PRIMARY KEY REFERENCES passages(id) ON DELETE CASCADE,
        provider_revision TEXT NOT NULL,
        vector_json TEXT NOT NULL,
        dimension INTEGER NOT NULL
    )""",
    """CREATE TABLE sidecar_embedding_state (
        sidecar_id TEXT PRIMARY KEY REFERENCES sidecar_index(id) ON DELETE CASCADE,
        provider_revision TEXT NOT NULL,
        status TEXT NOT NULL,
        embedded_count INTEGER NOT NULL,
        expected_count INTEGER NOT NULL,
        message TEXT,
        updated_at TEXT NOT NULL
    )""",
),))


def _state_tables(store: SidecarStore) -> Tables:
    def create(state: sqlite3.Connection) -> None:
        state.execute(
            """CREATE TABLE embedding_provider (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                api_key_env TEXT,
                allow_insecure_http INTEGER NOT NULL,
                revision TEXT NOT NULL,
                configured_at TEXT NOT NULL
            )"""
        )
        state.execute(
            """CREATE TABLE index_cleanup (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                token TEXT NOT NULL
            )"""
        )
        if store.legacy_path.is_file():
            _import_legacy(store, state)

    return Tables("evidence index", "embedding_provider", (create,))


def _import_legacy(store: SidecarStore, state: sqlite3.Connection) -> None:
    # Version 0.3.0 kept everything in one database. It stays in place as the backup.
    # The state step commits after the index copy, so a crash between the two
    # commits repeats the copy from the original.
    with read_only(store.legacy_path) as legacy:
        copy_rows(legacy, state, "embedding_provider",
                  "id, base_url, model, api_key_env, allow_insecure_http, revision, configured_at")
        with connect(store.index_path, _SEARCH_TABLES) as index:
            index.execute("BEGIN IMMEDIATE")
            index.execute("DELETE FROM sidecar_index")
            index.execute("DELETE FROM passages_fts")
            copy_rows(legacy, index, "sidecars", "id, status, updated_at", target="sidecar_index")
            copy_rows(legacy, index, "documents", "id, sidecar_id, relative_path, sha256, indexed_at, kind",
                      defaults={"kind": "'text'"})
            copy_rows(legacy, index, "passages", "id, document_id, start_line, end_line, context, page_number",
                      defaults={"page_number": "NULL"})
            copy_rows(legacy, index, "pdf_pages", "document_id, page_number, status, message")
            copy_rows(legacy, index, "sidecar_updates",
                      "sidecar_id, run_id, started_at, completed_at, last_success_at, added, changed, deleted, unchanged, issues_json")
            copy_rows(legacy, index, "passage_embeddings", "passage_id, provider_revision, vector_json, dimension")
            copy_rows(legacy, index, "sidecar_embedding_state",
                      "sidecar_id, provider_revision, status, embedded_count, expected_count, message, updated_at")
            index.execute("INSERT INTO passages_fts (evidence_id, context) SELECT id, context FROM passages")


@contextmanager
def open_state(store: SidecarStore) -> Iterator[sqlite3.Connection]:
    """Open the evidence index's tables in the state database."""
    try:
        with connect(store.db_path, _state_tables(store)) as connection:
            yield connection
    except (OSError, KeyError) as exc:
        raise StoreError(f"Cannot open sidecar state at {store.db_path}: {exc}") from exc


@contextmanager
def open_search(store: SidecarStore) -> Iterator[sqlite3.Connection]:
    """Open the search database, after any upgrade from version 0.3.0 has filled it."""
    with open_state(store):
        pass
    try:
        with connect(store.index_path, _SEARCH_TABLES) as connection:
            yield connection
    except OSError as exc:
        raise StoreError(f"Cannot open search index at {store.index_path}: {exc}") from exc


def mark_cleanup(state_connection: sqlite3.Connection) -> None:
    """Record, in the caller's state transaction, that outdated embeddings need removal."""
    state_connection.execute("INSERT OR REPLACE INTO index_cleanup VALUES (1, ?)", (str(uuid.uuid4()),))


def remove_outdated(store: SidecarStore) -> None:
    """Remove index data for sidecars no longer registered and for outdated provider revisions.

    Safe to call at any time. Work left by an interrupted removal or provider
    change is found again on the next call.
    """
    try:
        with open_state(store) as connection:
            pending = connection.execute("SELECT token FROM index_cleanup").fetchone()
        with open_search(store) as index:
            indexed = {row["id"] for row in index.execute("SELECT id FROM sidecar_index")}
        if pending is None and indexed <= store.ids():
            return
        with open_search(store) as index:
            index.execute("BEGIN IMMEDIATE")
            active = store.ids()
            with open_state(store) as connection:
                connection.execute("BEGIN")
                pending = connection.execute("SELECT token FROM index_cleanup").fetchone()
                provider = connection.execute("SELECT revision FROM embedding_provider").fetchone()
            index.executemany(
                "DELETE FROM sidecar_index WHERE id = ?",
                [(row["id"],) for row in index.execute("SELECT id FROM sidecar_index") if row["id"] not in active],
            )
            if pending is not None:
                for table in ("passage_embeddings", "sidecar_embedding_state"):
                    if provider is None:
                        index.execute(f"DELETE FROM {table}")
                    else:
                        index.execute(f"DELETE FROM {table} WHERE provider_revision != ?", (provider["revision"],))
        if pending is not None:
            with open_state(store) as connection:
                connection.execute("DELETE FROM index_cleanup WHERE token = ?", (pending["token"],))
    except (sqlite3.Error, StoreError):
        # The state commit already took effect. Retry when indexing resumes.
        return
