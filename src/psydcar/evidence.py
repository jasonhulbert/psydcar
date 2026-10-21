"""Local text, code, and PDF indexing with source-backed evidence retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .embeddings import (
    EmbeddingClient, EmbeddingError, ProviderConfig, ProviderStore, _state_schema, _vector_schema,
    normalize_vector,
)
from .pdf_text import PdfTextError, extract_pdf_page, extract_pdf_pages
from .store import Sidecar, SidecarStore, StoreError


SUPPORTED_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".jsx", ".ts", ".tsx",
        ".json", ".yaml", ".yml", ".toml", ".go", ".rs", ".java", ".c", ".h",
        ".cpp", ".hpp", ".cs", ".sh", ".sql", ".html", ".css",
    }
)
IGNORED_DIRECTORIES = frozenset({"node_modules", "__pycache__", "dist", "build", "target", "vendor"})
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
CHUNK_LINES = 40
CHUNK_OVERLAP = 5


@dataclass(frozen=True)
class IndexReport:
    sidecar: Sidecar
    files: int
    passages: int
    skipped: tuple[str, ...]
    pdf_page_notices: tuple[str, ...] = ()
    embeddings: int = 0
    embedding_status: str = "not_configured"
    provider_error: str | None = None
    added: int = 0
    changed: int = 0
    deleted: int = 0
    unchanged: int = 0
    deleted_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ScannedDocument:
    relative_path: str
    sha256: str
    kind: str
    passages: list[tuple[int, int, str, int | None]]
    pdf_page_notices: list[tuple[int, str, str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            sidecar_id TEXT NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
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
        END;"""
    )
    # Add PDF fields to stores created by the text/code milestone.
    document_columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    if "kind" not in document_columns:
        connection.execute("ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")
    passage_columns = {row[1] for row in connection.execute("PRAGMA table_info(passages)")}
    if "page_number" not in passage_columns:
        connection.execute("ALTER TABLE passages ADD COLUMN page_number INTEGER")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS sidecar_updates (
            sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            last_success_at TEXT,
            added INTEGER NOT NULL DEFAULT 0,
            changed INTEGER NOT NULL DEFAULT 0,
            deleted INTEGER NOT NULL DEFAULT 0,
            unchanged INTEGER NOT NULL DEFAULT 0,
            issues_json TEXT NOT NULL DEFAULT '[]'
        )"""
    )


def _read_source(path: Path, root: Path | None = None) -> tuple[bytes | None, str, str | None]:
    """Read a regular UTF-8 file once; return bytes, status, and a clear failure reason."""
    data, status, reason = _read_bytes(path, root)
    if data is None:
        return None, status, reason
    if b"\0" in data:
        return None, "unreadable", "Source contains binary data."
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "unreadable", "Source is not UTF-8 text."
    return data, "current", None


def _read_bytes(path: Path, root: Path | None = None) -> tuple[bytes | None, str, str | None]:
    """Read current bytes while enforcing the source-tree boundary."""
    try:
        if path.is_symlink():
            return None, "unreadable", "Source is a symbolic link."
        if root is not None and not path.resolve(strict=False).is_relative_to(root):
            return None, "unreadable", "Source path no longer stays inside the sidecar tree."
        data = path.read_bytes()
    except FileNotFoundError:
        return None, "missing", "Source file no longer exists."
    except OSError as exc:
        return None, "unreadable", f"Source file cannot be read: {exc.strerror or exc}"
    except RuntimeError as exc:
        return None, "unreadable", f"Source path cannot be resolved: {exc}"
    return data, "current", None


def _source_path(sidecar: Sidecar, relative_path: str) -> Path:
    # The relative path comes from our own index, but still enforce the sidecar boundary.
    root = sidecar.source_path
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StoreError(f"Indexed source is outside the sidecar tree: {relative_path}")
    return root / relative


def _chunks(text: str) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    if not lines:
        return []
    result = []
    step = CHUNK_LINES - CHUNK_OVERLAP
    for start in range(0, len(lines), step):
        end = min(start + CHUNK_LINES, len(lines))
        context = "\n".join(lines[start:end])
        if context.strip():
            result.append((start + 1, end, context))
        if end == len(lines):
            break
    return result


def _walk_candidates(root: Path, onerror: Callable[[OSError], None]) -> Iterator[tuple[Path, bool]]:
    """Yield files in the manual updater's scope and linked directories it must not enter."""
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        traversable = []
        for name in dirs:
            if name.startswith(".") or name in IGNORED_DIRECTORIES:
                continue
            child = Path(directory) / name
            if child.is_symlink():
                yield child, True
            else:
                traversable.append(name)
        dirs[:] = sorted(traversable)
        for name in sorted(files):
            extension = Path(name).suffix.lower()
            if name.startswith(".") or (extension not in SUPPORTED_EXTENSIONS and extension != ".pdf"):
                continue
            yield Path(directory) / name, False


def source_snapshot(sidecar: Sidecar) -> tuple[tuple[str, str, int, int, int, int], ...]:
    """Cheap watcher fingerprint with the updater's exact source scope."""
    root = sidecar.source_path
    if root.is_symlink():
        return ((".", "linked_root", 0, 0, 0, 0),)
    if not root.is_dir():
        return ((".", "missing_root", 0, 0, 0, 0),)
    entries: list[tuple[str, str, int, int, int, int]] = []

    def walk_error(error: OSError) -> None:
        entries.append((str(error.filename or root), "walk_error", error.errno or 0, 0, 0, 0))

    for path, linked_directory in _walk_candidates(root, walk_error):
        relative = path.relative_to(root).as_posix()
        try:
            stat = path.lstat()
        except OSError as exc:
            entries.append((relative, "stat_error", exc.errno or 0, 0, 0, 0))
            continue
        entries.append((
            relative, "linked_directory" if linked_directory else "file",
            stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
        ))
    return tuple(sorted(entries))


def _scan(sidecar: Sidecar) -> tuple[list[_ScannedDocument], list[str], set[str], set[str]]:
    root = sidecar.source_path
    if root.is_symlink() or not root.is_dir():
        raise StoreError(f"Source directory is missing, a symbolic link, or not a directory: {root}")
    documents = []
    skipped = []
    seen: set[str] = set()
    blocked_directories: set[str] = set()

    def walk_error(error: OSError) -> None:
        skipped.append(f"{error.filename or root}: {error.strerror or error}")
        if error.filename:
            try:
                blocked_directories.add(Path(error.filename).relative_to(root).as_posix())
            except ValueError:
                blocked_directories.add(".")
        else:
            blocked_directories.add(".")

    for path, linked_directory in _walk_candidates(root, walk_error):
        relative = path.relative_to(root).as_posix()
        if linked_directory:
            blocked_directories.add(relative)
            skipped.append(f"{relative}: source directory is a symbolic link")
            continue
        seen.add(relative)
        if path.is_symlink():
            skipped.append(f"{relative}: source is a symbolic link")
            continue
        extension = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped.append(f"{relative}: {exc.strerror or exc}")
            continue
        size_limit = MAX_PDF_BYTES if extension == ".pdf" else MAX_FILE_BYTES
        if size > size_limit:
            limit_name = "50 MiB PDF" if extension == ".pdf" else "2 MiB text"
            skipped.append(f"{relative}: exceeds the {limit_name} file limit")
            continue
        data, status, reason = _read_bytes(path, root) if extension == ".pdf" else _read_source(path, root)
        if data is None:
            skipped.append(f"{relative}: {reason or status}")
            continue
        digest = hashlib.sha256(data).hexdigest()
        if extension == ".pdf":
            try:
                pages, no_text_pages, page_errors = extract_pdf_pages(data)
            except PdfTextError as exc:
                skipped.append(f"{relative}: {exc}")
                continue
            passages = [
                (start, end, context, page.number)
                for page in pages for start, end, context in _chunks(page.text)
            ]
            notices = [(number, "no_text", "No searchable text; OCR is not available.") for number in no_text_pages]
            notices.extend((number, "error", f"Text extraction failed: {reason}") for number, reason in page_errors)
            skipped.extend(f"{relative}, page {number}: text extraction failed: {reason}" for number, reason in page_errors)
            documents.append(_ScannedDocument(relative, digest, "pdf", passages, notices))
        else:
            text = data.decode("utf-8-sig")
            passages = [(start, end, context, None) for start, end, context in _chunks(text)]
            documents.append(_ScannedDocument(relative, digest, "text", passages, []))
    return documents, skipped, seen, blocked_directories


def _resolve_selection(store: SidecarStore, references: list[str]) -> list[Sidecar]:
    if not references:
        raise StoreError("Select at least one sidecar with --sidecar.")
    selected = {}
    for reference in references:
        sidecar = store.get(reference)
        selected[sidecar.id] = sidecar
    return list(selected.values())


class EvidenceStore:
    """Shared indexing, search, and retrieval API for selected sidecars."""

    def __init__(self, sidecars: SidecarStore | None = None):
        self.sidecars = sidecars or SidecarStore()
        self.last_search_warning: str | None = None

    def index(self, reference: str) -> IndexReport:
        """Keep the original CLI/API name for a manual update."""
        return self.update(reference)

    def update(self, reference: str) -> IndexReport:
        """Refresh changed sources while retaining prior evidence for failed reads."""
        sidecar = self.sidecars.get(reference)
        provider = ProviderStore(self.sidecars).get()
        run_id = str(uuid.uuid4())
        started_at = _now()
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                connection.execute(
                    """INSERT INTO sidecar_updates (sidecar_id, run_id, started_at)
                       VALUES (?, ?, ?) ON CONFLICT(sidecar_id) DO UPDATE SET
                       run_id=excluded.run_id, started_at=excluded.started_at,
                       completed_at=NULL, issues_json='[]'""",
                    (sidecar.id, run_id, started_at),
                )
                connection.execute(
                    "UPDATE sidecars SET status = 'update_pending', updated_at = ? WHERE id = ?",
                    (started_at, sidecar.id),
                )
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot start sidecar update: {exc}") from exc
        try:
            documents, skipped, seen, blocked_directories = _scan(sidecar)
        except StoreError as exc:
            self._finish_update(sidecar.id, run_id, "index_error", [str(exc)])
            raise
        except Exception as exc:
            message = f"Source scan failed ({type(exc).__name__}). Retry this update."
            self._finish_update(sidecar.id, run_id, "index_error", [message])
            raise StoreError(message) from exc
        reuse_embeddings = False
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                self._check_run(connection, sidecar.id, run_id)
                old_rows = connection.execute(
                    "SELECT id, relative_path, sha256, kind FROM documents WHERE sidecar_id = ?",
                    (sidecar.id,),
                ).fetchall()
                old = {row["relative_path"]: row for row in old_rows}
                added = changed = deleted = unchanged = 0
                deleted_paths: list[str] = []
                for relative_path, row in old.items():
                    if relative_path in seen or any(
                        blocked == "." or relative_path == blocked or relative_path.startswith(blocked + "/")
                        for blocked in blocked_directories
                    ):
                        continue
                    connection.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
                    deleted += 1
                    deleted_paths.append(relative_path)
                indexed_at = _now()
                for document in documents:
                    previous = old.get(document.relative_path)
                    if previous is not None and previous["sha256"] == document.sha256 and previous["kind"] == document.kind:
                        unchanged += 1
                        continue
                    if previous is not None:
                        connection.execute("DELETE FROM documents WHERE id = ?", (previous["id"],))
                        changed += 1
                    else:
                        added += 1
                    cursor = connection.execute(
                        "INSERT INTO documents (sidecar_id, relative_path, sha256, indexed_at, kind) VALUES (?, ?, ?, ?, ?)",
                        (sidecar.id, document.relative_path, document.sha256, indexed_at, document.kind),
                    )
                    for page_number, status, message in document.pdf_page_notices:
                        connection.execute(
                            "INSERT INTO pdf_pages (document_id, page_number, status, message) VALUES (?, ?, ?, ?)",
                            (cursor.lastrowid, page_number, status, message),
                        )
                    for start, end, context, page_number in document.passages:
                        evidence_id = str(uuid.uuid4())
                        connection.execute(
                            "INSERT INTO passages (id, document_id, start_line, end_line, context, page_number) VALUES (?, ?, ?, ?, ?, ?)",
                            (evidence_id, cursor.lastrowid, start, end, context, page_number),
                        )
                        connection.execute(
                            "INSERT INTO passages_fts (evidence_id, context) VALUES (?, ?)", (evidence_id, context)
                        )
                passage_count = connection.execute(
                    """SELECT count(*) FROM passages p JOIN documents d ON d.id = p.document_id
                       WHERE d.sidecar_id = ?""", (sidecar.id,),
                ).fetchone()[0]
                if provider is not None and not (added or changed or deleted):
                    _state_schema(connection)
                    state = connection.execute(
                        "SELECT * FROM sidecar_embedding_state WHERE sidecar_id = ?", (sidecar.id,)
                    ).fetchone()
                    reuse_embeddings = state is not None and (
                        state["provider_revision"] == provider.revision
                        and state["status"] == "complete"
                        and state["embedded_count"] == passage_count
                        and state["expected_count"] == passage_count
                    )
                connection.execute(
                    "UPDATE sidecars SET status = ?, updated_at = ? WHERE id = ?",
                    ("embedding_pending" if provider else "update_pending", indexed_at, sidecar.id),
                )
                connection.execute(
                    """UPDATE sidecar_updates SET added = ?, changed = ?, deleted = ?, unchanged = ?,
                       issues_json = ? WHERE sidecar_id = ? AND run_id = ?""",
                    (added, changed, deleted, unchanged,
                     json.dumps([*skipped, *(f"Removed source: {path}" for path in deleted_paths)]),
                     sidecar.id, run_id),
                )
        except sqlite3.Error as exc:
            self._finish_update(sidecar.id, run_id, "index_error", [f"Cannot apply sidecar update: {exc}"])
            raise StoreError(f"Cannot index sidecar: {exc}") from exc
        pdf_notices = tuple(
            f"{document.relative_path}, page {number}: {message}"
            for document in documents for number, _, message in document.pdf_page_notices
        )
        embedded_count = 0
        embedding_status = "not_configured"
        provider_error = None
        if provider is not None:
            if reuse_embeddings:
                embedded_count = passage_count
                embedding_status = "complete"
            else:
                try:
                    embedded_count, provider_error = self._build_embeddings(sidecar.id, provider, run_id)
                except StoreError as exc:
                    self._finish_update(sidecar.id, run_id, "embedding_error", [*skipped, str(exc)])
                    raise
                except Exception as exc:
                    message = f"Embedding processing failed ({type(exc).__name__}). Retry this update."
                    self._finish_update(sidecar.id, run_id, "embedding_error", [*skipped, message])
                    raise StoreError(message) from exc
                embedding_status = "error" if provider_error else "complete"
        final_status = (
            "index_error" if skipped else "embedding_error" if provider_error
            else "source_attention" if deleted else "indexed"
        )
        self._finish_update(
            sidecar.id, run_id, final_status,
            [*skipped, *([provider_error] if provider_error else []),
             *(f"Removed source: {path}" for path in deleted_paths)],
        )
        return IndexReport(
            self.sidecars.get(sidecar.id), len(documents), passage_count, tuple(skipped), pdf_notices,
            embedded_count, embedding_status, provider_error, added, changed, deleted, unchanged,
            tuple(deleted_paths),
        )

    @staticmethod
    def _check_run(connection: sqlite3.Connection, sidecar_id: str, run_id: str) -> None:
        row = connection.execute("SELECT run_id FROM sidecar_updates WHERE sidecar_id = ?", (sidecar_id,)).fetchone()
        if row is None or row["run_id"] != run_id:
            raise StoreError("A newer update replaced this sidecar update. Its results were not applied.")

    def _finish_update(self, sidecar_id: str, run_id: str, status: str, issues: list[str]) -> None:
        now = _now()
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                self._check_run(connection, sidecar_id, run_id)
                connection.execute(
                    """UPDATE sidecar_updates SET completed_at = ?, issues_json = ?,
                       last_success_at = CASE WHEN ? IN ('indexed', 'source_attention')
                           THEN ? ELSE last_success_at END
                       WHERE sidecar_id = ? AND run_id = ?""",
                    (now, json.dumps(issues), status, now, sidecar_id, run_id),
                )
                connection.execute(
                    "UPDATE sidecars SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, sidecar_id),
                )
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot record sidecar update status: {exc}") from exc

    def update_state(self, reference: str) -> dict[str, Any]:
        """Return persisted update details for the CLI, watcher, and dashboard."""
        sidecar = self.sidecars.get(reference)
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                row = connection.execute(
                    "SELECT * FROM sidecar_updates WHERE sidecar_id = ?", (sidecar.id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot inspect sidecar update: {exc}") from exc
        return {
            "sidecar_id": sidecar.id,
            "status": sidecar.status,
            "state": self._processing_state(sidecar.status),
            "started_at": row["started_at"] if row else None,
            "completed_at": row["completed_at"] if row else None,
            "last_success_at": row["last_success_at"] if row else None,
            "added": row["added"] if row else 0,
            "changed": row["changed"] if row else 0,
            "deleted": row["deleted"] if row else 0,
            "unchanged": row["unchanged"] if row else 0,
            "issues": json.loads(row["issues_json"]) if row else [],
        }

    def coverage(self, reference: str) -> dict[str, Any]:
        """Summarize indexed source coverage for management surfaces."""
        sidecar = self.sidecars.get(reference)
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                row = connection.execute(
                    """SELECT count(*) AS files,
                              coalesce(sum(CASE WHEN kind = 'pdf' THEN 1 ELSE 0 END), 0) AS pdfs,
                              coalesce(sum(CASE WHEN kind = 'text' THEN 1 ELSE 0 END), 0) AS text_files,
                              max(indexed_at) AS latest_indexed_at
                       FROM documents WHERE sidecar_id = ?""",
                    (sidecar.id,),
                ).fetchone()
                passages = connection.execute(
                    """SELECT count(*) FROM passages p JOIN documents d ON d.id = p.document_id
                       WHERE d.sidecar_id = ?""",
                    (sidecar.id,),
                ).fetchone()[0]
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot inspect sidecar coverage: {exc}") from exc
        return {
            "files": row["files"], "text_files": row["text_files"], "pdfs": row["pdfs"],
            "passages": passages, "latest_indexed_at": row["latest_indexed_at"],
        }

    @staticmethod
    def _processing_state(status: str) -> str:
        if status in {"update_pending", "embedding_pending"}:
            return "pending"
        if status in {"index_error", "embedding_error", "source_attention"}:
            return "attention"
        return "current" if status == "indexed" else "not_indexed"

    def _build_embeddings(self, sidecar_id: str, provider: ProviderConfig, run_id: str) -> tuple[int, str | None]:
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                self._check_run(connection, sidecar_id, run_id)
                _vector_schema(connection)
                _state_schema(connection)
                rows = connection.execute(
                    """SELECT p.id, p.context FROM passages p JOIN documents d ON d.id = p.document_id
                       WHERE d.sidecar_id = ? ORDER BY d.relative_path, p.page_number, p.start_line""",
                    (sidecar_id,),
                ).fetchall()
                connection.execute(
                    """INSERT OR REPLACE INTO sidecar_embedding_state
                       (sidecar_id, provider_revision, status, embedded_count, expected_count, message, updated_at)
                       VALUES (?, ?, 'pending', 0, ?, NULL, ?)""",
                    (sidecar_id, provider.revision, len(rows), _now()),
                )
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot prepare embeddings: {exc}") from exc
        client = EmbeddingClient(provider)
        embedded_count = 0
        dimension = None
        try:
            for start in range(0, len(rows), 16):
                with self.sidecars._connect() as connection:
                    self._check_run(connection, sidecar_id, run_id)
                    active = connection.execute("SELECT revision FROM embedding_provider WHERE id = 1").fetchone()
                    if active is None or active["revision"] != provider.revision:
                        raise EmbeddingError("Provider settings changed during indexing. Reindex this sidecar.")
                batch = rows[start : start + 16]
                vectors = client.embed([row["context"] for row in batch])
                for vector in vectors:
                    if dimension is None:
                        dimension = len(vector)
                    elif len(vector) != dimension:
                        raise EmbeddingError("Embedding provider changed vector dimensions during indexing.")
                with self.sidecars._connect() as connection:
                    self._check_run(connection, sidecar_id, run_id)
                    active = connection.execute("SELECT revision FROM embedding_provider WHERE id = 1").fetchone()
                    if active is None or active["revision"] != provider.revision:
                        raise EmbeddingError("Provider settings changed during indexing. Reindex this sidecar.")
                    _vector_schema(connection)
                    for row, vector in zip(batch, vectors):
                        connection.execute(
                            """INSERT INTO passage_embeddings (passage_id, provider_revision, vector_json, dimension)
                               VALUES (?, ?, ?, ?) ON CONFLICT(passage_id) DO UPDATE SET
                               provider_revision=excluded.provider_revision, vector_json=excluded.vector_json,
                               dimension=excluded.dimension""",
                            (row["id"], provider.revision, json.dumps(vector), len(vector)),
                        )
                    embedded_count += len(batch)
                    connection.execute(
                        "UPDATE sidecar_embedding_state SET embedded_count = ?, updated_at = ? WHERE sidecar_id = ? AND provider_revision = ?",
                        (embedded_count, _now(), sidecar_id, provider.revision),
                    )
            with self.sidecars._connect() as connection:
                self._check_run(connection, sidecar_id, run_id)
                active = connection.execute("SELECT revision FROM embedding_provider WHERE id = 1").fetchone()
                if active is None or active["revision"] != provider.revision:
                    raise EmbeddingError("Provider settings changed during indexing. Reindex this sidecar.")
                connection.execute(
                    "UPDATE sidecar_embedding_state SET status = 'complete', message = NULL, updated_at = ? WHERE sidecar_id = ? AND provider_revision = ?",
                    (_now(), sidecar_id, provider.revision),
                )
        except EmbeddingError as exc:
            message = str(exc)
            with self.sidecars._connect() as connection:
                self._check_run(connection, sidecar_id, run_id)
                active = connection.execute("SELECT revision FROM embedding_provider WHERE id = 1").fetchone()
                if active is not None and active["revision"] == provider.revision:
                    connection.execute(
                        "UPDATE sidecar_embedding_state SET status = 'error', message = ?, updated_at = ? WHERE sidecar_id = ? AND provider_revision = ?",
                        (message, _now(), sidecar_id, provider.revision),
                    )
            return embedded_count, message
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot store embeddings: {exc}") from exc
        return embedded_count, None

    def pdf_page_notices(self, reference: str) -> list[dict[str, Any]]:
        """Return persisted page notices so users can inspect them after restart."""
        sidecar = self.sidecars.get(reference)
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                rows = connection.execute(
                    """SELECT d.relative_path, p.page_number, p.status, p.message
                       FROM pdf_pages p JOIN documents d ON d.id = p.document_id
                       WHERE d.sidecar_id = ? ORDER BY d.relative_path, p.page_number""",
                    (sidecar.id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot inspect PDF page notices: {exc}") from exc
        return [
            {"source": row["relative_path"], "page": row["page_number"], "status": row["status"], "message": row["message"]}
            for row in rows
        ]

    def search(self, references: list[str], query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.last_search_warning = None
        selected = _resolve_selection(self.sidecars, references)
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            raise StoreError("Search query must contain at least one word or number.")
        if not 1 <= limit <= 100:
            raise StoreError("Search limit must be between 1 and 100.")
        expression = " OR ".join('"' + term + '"' for term in terms[:20])
        selected_by_id = {item.id: item for item in selected}
        placeholders = ", ".join("?" for _ in selected)
        provider = ProviderStore(self.sidecars).get()
        vector_rows: list[sqlite3.Row] = []
        embeddings_ready = False
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                lexical_rows = connection.execute(
                    f"""SELECT p.id, p.start_line, p.end_line, p.context, p.page_number, d.sidecar_id,
                               d.relative_path, d.sha256, d.indexed_at, d.kind, bm25(passages_fts) AS score
                        FROM passages_fts
                        JOIN passages p ON p.id = passages_fts.evidence_id
                        JOIN documents d ON d.id = p.document_id
                        WHERE passages_fts MATCH ? AND d.sidecar_id IN ({placeholders})
                        ORDER BY score, d.relative_path, p.start_line LIMIT ?""",
                    (expression, *selected_by_id, max(limit * 10, 100)),
                ).fetchall()
                if provider is not None:
                    _state_schema(connection)
                    _vector_schema(connection)
                    states = connection.execute(
                        f"SELECT * FROM sidecar_embedding_state WHERE sidecar_id IN ({placeholders})",
                        tuple(selected_by_id),
                    ).fetchall()
                    embeddings_ready = len(states) == len(selected_by_id) and all(
                        row["provider_revision"] == provider.revision
                        and row["status"] == "complete"
                        and row["embedded_count"] == row["expected_count"]
                        for row in states
                    )
                    if embeddings_ready:
                        vector_rows = connection.execute(
                            f"""SELECT p.id, p.start_line, p.end_line, p.context, p.page_number, d.sidecar_id,
                                       d.relative_path, d.sha256, d.indexed_at, d.kind, e.vector_json, e.dimension
                                FROM passage_embeddings e
                                JOIN passages p ON p.id = e.passage_id
                                JOIN documents d ON d.id = p.document_id
                                WHERE d.sidecar_id IN ({placeholders}) AND e.provider_revision = ?""",
                            (*selected_by_id, provider.revision),
                        ).fetchall()
                        embeddings_ready = len(vector_rows) == sum(row["expected_count"] for row in states)
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot search sidecars: {exc}") from exc
        mode = "lexical"
        lexical_ranks = {row["id"]: rank for rank, row in enumerate(lexical_rows, 1)}
        semantic_ranks: dict[str, int] = {}
        candidates = {row["id"]: row for row in lexical_rows}
        if provider is not None and not embeddings_ready:
            self.last_search_warning = "Embeddings are pending or incomplete for selected sidecars; using lexical search."
        if provider is not None and embeddings_ready and vector_rows:
            try:
                query_vector = EmbeddingClient(provider).embed([query])[0]
                if any(row["dimension"] != len(query_vector) for row in vector_rows):
                    raise EmbeddingError("Stored embedding dimensions differ from the provider. Reindex selected sidecars.")
                semantic = sorted(
                    (
                        (sum(left * right for left, right in zip(query_vector, normalize_vector(json.loads(row["vector_json"])))), row)
                        for row in vector_rows
                    ),
                    key=lambda item: (-item[0], item[1]["relative_path"], item[1]["id"]),
                )[: max(limit * 10, 100)]
                semantic_ranks = {row["id"]: rank for rank, (_, row) in enumerate(semantic, 1)}
                candidates.update({row["id"]: row for _, row in semantic})
                mode = "hybrid"
            except (EmbeddingError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.last_search_warning = (
                    str(exc) if isinstance(exc, EmbeddingError)
                    else "Stored embeddings are invalid; reindex selected sidecars."
                )
        if mode == "hybrid":
            ranked_ids = sorted(
                candidates,
                key=lambda evidence_id: (
                    -(1 / (60 + lexical_ranks[evidence_id]) if evidence_id in lexical_ranks else 0)
                    -(1 / (60 + semantic_ranks[evidence_id]) if evidence_id in semantic_ranks else 0),
                    candidates[evidence_id]["relative_path"], evidence_id,
                ),
            )[:limit]
            rows = [candidates[evidence_id] for evidence_id in ranked_ids]
        else:
            rows = lexical_rows[:limit]
        results = []
        for row in rows:
            sidecar = self.sidecars.get(row["sidecar_id"])
            path = _source_path(sidecar, row["relative_path"])
            data, source_status, _ = (
                _read_bytes(path, sidecar.source_path) if row["kind"] == "pdf"
                else _read_source(path, sidecar.source_path)
            )
            if data is None:
                freshness = source_status
            elif row["kind"] == "pdf":
                try:
                    extract_pdf_page(data, row["page_number"])
                except PdfTextError as exc:
                    if str(exc).startswith("PDF support requires"):
                        raise StoreError(str(exc)) from exc
                    freshness = "unreadable"
                else:
                    freshness = "current" if hashlib.sha256(data).hexdigest() == row["sha256"] else "changed"
            else:
                freshness = "current" if hashlib.sha256(data).hexdigest() == row["sha256"] else "changed"
            evidence = self._evidence(row, sidecar, row["context"], freshness, "index")
            evidence["search"] = {
                "mode": mode,
                "lexical_rank": lexical_ranks.get(row["id"]),
                "semantic_rank": semantic_ranks.get(row["id"]),
            }
            if self.last_search_warning:
                evidence["search"]["notice"] = self.last_search_warning
            results.append(evidence)
        return results

    def retrieve(self, references: list[str], evidence_id: str) -> dict[str, Any]:
        selected = _resolve_selection(self.sidecars, references)
        selected_by_id = {item.id: item for item in selected}
        placeholders = ", ".join("?" for _ in selected)
        try:
            with self.sidecars._connect() as connection:
                _schema(connection)
                row = connection.execute(
                    f"""SELECT p.id, p.start_line, p.end_line, p.context, p.page_number, d.sidecar_id,
                               d.relative_path, d.sha256, d.indexed_at, d.kind
                        FROM passages p JOIN documents d ON d.id = p.document_id
                        WHERE p.id = ? AND d.sidecar_id IN ({placeholders})""",
                    (evidence_id, *selected_by_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot retrieve evidence: {exc}") from exc
        if row is None:
            raise StoreError("Evidence was not found in the selected sidecars.")
        sidecar = self.sidecars.get(row["sidecar_id"])
        path = _source_path(sidecar, row["relative_path"])
        data, source_status, reason = (
            _read_bytes(path, sidecar.source_path) if row["kind"] == "pdf"
            else _read_source(path, sidecar.source_path)
        )
        if data is None:
            evidence = self._evidence(row, sidecar, None, source_status, None)
            evidence["message"] = reason
            return evidence
        pdf_text = None
        if row["kind"] == "pdf":
            try:
                pdf_text = extract_pdf_page(data, row["page_number"])
            except PdfTextError as exc:
                if str(exc).startswith("PDF support requires"):
                    raise StoreError(str(exc)) from exc
                evidence = self._evidence(row, sidecar, None, "unreadable", None)
                evidence["message"] = str(exc)
                return evidence
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            evidence = self._evidence(row, sidecar, None, "changed", None)
            evidence["message"] = "Source changed since indexing. Reindex this sidecar to retrieve current evidence."
            return evidence
        lines = (pdf_text if pdf_text is not None else data.decode("utf-8-sig")).splitlines()
        context = "\n".join(lines[row["start_line"] - 1 : row["end_line"]])
        if row["kind"] == "pdf" and context != row["context"]:
            evidence = self._evidence(row, sidecar, None, "unreadable", None)
            evidence["message"] = "PDF text extraction changed since indexing. Reindex this sidecar."
            return evidence
        return self._evidence(row, sidecar, context, "current", "source")

    @staticmethod
    def _evidence(row: sqlite3.Row, sidecar: Sidecar, context: str | None, freshness: str, origin: str | None) -> dict[str, Any]:
        processing_state = EvidenceStore._processing_state(sidecar.status)
        return {
            "evidence_id": row["id"],
            "sidecar": {"id": sidecar.id, "name": sidecar.name},
            "source": {
                "path": str(sidecar.source_path / row["relative_path"]),
                "relative_path": row["relative_path"],
                "indexed_sha256": row["sha256"],
            },
            "location": (
                {"kind": "page", "page": row["page_number"]} if row["kind"] == "pdf"
                else {"kind": "lines", "start": row["start_line"], "end": row["end_line"]}
            ),
            "context": context,
            "context_origin": origin,
            "freshness": freshness,
            "index_freshness": "current" if freshness == "current" and processing_state == "current" else "stale",
            "processing": {"state": processing_state, "status": sidecar.status},
            "indexed_at": row["indexed_at"],
        }
