"""SQLite-backed document, chunk, and keyword search index."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ksidecar.scanner import FileCandidate, scan_files
from ksidecar.sidecars import Sidecar, SidecarRegistry

INDEX_FILENAME = "index.sqlite"
SCHEMA_VERSION = 1
DEFAULT_CHUNK_LINES = 140
DEFAULT_CHUNK_OVERLAP_LINES = 20
MARKDOWN_EXTENSIONS = frozenset({".md", ".mdx"})
JSON_YAML_EXTENSIONS = frozenset({".json", ".yaml", ".yml"})
ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


class IndexError(RuntimeError):
    """Base exception for indexing failures."""


@dataclass(frozen=True)
class DocumentRecord:
    id: int
    relative_path: str
    extension: str
    size_bytes: int
    modified_at: float
    content_hash: str
    status: str


@dataclass(frozen=True)
class ChunkRecord:
    relative_path: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


@dataclass(frozen=True)
class IndexingErrorRecord:
    relative_path: str
    stage: str
    message: str


@dataclass(frozen=True)
class RebuildResult:
    document_count: int
    chunk_count: int
    error_count: int


@dataclass(frozen=True)
class SearchResult:
    chunk_id: int
    relative_path: str
    start_line: int
    end_line: int
    preview: str
    score: float


def index_path_for_sidecar(registry: SidecarRegistry, sidecar_id: str) -> Path:
    return registry.storage_dir(sidecar_id) / INDEX_FILENAME


def connect_index(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            extension TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_at REAL NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            relative_path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            text TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            relative_path,
            text
        );

        CREATE TABLE IF NOT EXISTS indexing_errors (
            id INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL,
            stage TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
        """
    )
    connection.commit()


def rebuild_sidecar_index(
    registry: SidecarRegistry,
    sidecar_id: str,
) -> RebuildResult:
    sidecar = registry.get(sidecar_id)
    with connect_index(index_path_for_sidecar(registry, sidecar.id)) as connection:
        init_schema(connection)
        return rebuild_index(connection, sidecar)


def rebuild_index(connection: sqlite3.Connection, sidecar: Sidecar) -> RebuildResult:
    init_schema(connection)
    connection.execute("DELETE FROM chunks_fts")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM documents")
    connection.execute("DELETE FROM indexing_errors")

    document_count = 0
    chunk_count = 0
    error_count = 0

    for candidate in scan_files(
        sidecar.root_path,
        max_file_size_bytes=sidecar.config.max_file_size_bytes,
    ):
        try:
            content = read_text_content(candidate.path)
            content_hash = hash_text(content)
            document_id = insert_document(connection, candidate, content_hash)
            document_count += 1
            chunks = chunk_content(candidate.relative_path, candidate.extension, content)
            for chunk in chunks:
                insert_chunk(connection, document_id, chunk)
                chunk_count += 1
        except Exception as exc:  # noqa: BLE001 - errors should be captured per file.
            error_count += 1
            insert_indexing_error(
                connection,
                IndexingErrorRecord(
                    relative_path=path_to_index_string(candidate.relative_path),
                    stage="index",
                    message=str(exc),
                ),
            )

    connection.commit()
    return RebuildResult(
        document_count=document_count,
        chunk_count=chunk_count,
        error_count=error_count,
    )


def insert_document(
    connection: sqlite3.Connection,
    candidate: FileCandidate,
    content_hash: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO documents(
            relative_path,
            extension,
            size_bytes,
            modified_at,
            content_hash,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            path_to_index_string(candidate.relative_path),
            candidate.extension,
            candidate.size_bytes,
            candidate.path.stat().st_mtime,
            content_hash,
            "indexed",
        ),
    )
    return int(cursor.lastrowid)


def insert_chunk(
    connection: sqlite3.Connection,
    document_id: int,
    chunk: ChunkRecord,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO chunks(
            document_id,
            relative_path,
            start_line,
            end_line,
            text,
            content_hash
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            chunk.relative_path,
            chunk.start_line,
            chunk.end_line,
            chunk.text,
            chunk.content_hash,
        ),
    )
    chunk_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO chunks_fts(rowid, chunk_id, relative_path, text)
        VALUES (?, ?, ?, ?)
        """,
        (chunk_id, chunk_id, chunk.relative_path, chunk.text),
    )
    return chunk_id


def insert_indexing_error(
    connection: sqlite3.Connection,
    error: IndexingErrorRecord,
) -> None:
    connection.execute(
        """
        INSERT INTO indexing_errors(relative_path, stage, message)
        VALUES (?, ?, ?)
        """,
        (error.relative_path, error.stage, error.message),
    )


def read_text_content(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IndexError(f"could not decode text file: {path}")


def chunk_content(relative_path: Path, extension: str, content: str) -> list[ChunkRecord]:
    normalized_extension = extension.lower()
    if normalized_extension in MARKDOWN_EXTENSIONS:
        return chunk_markdown(relative_path, content)
    if normalized_extension in JSON_YAML_EXTENSIONS:
        return chunk_line_ranges(relative_path, content)
    return chunk_line_ranges(relative_path, content)


def chunk_markdown(relative_path: Path, content: str) -> list[ChunkRecord]:
    lines = split_lines(content)
    if not lines:
        return []

    heading_indexes = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^#{1,6}\s+\S", line)
    ]
    if not heading_indexes:
        return chunk_line_ranges(relative_path, content)

    starts = heading_indexes
    if starts[0] != 0:
        starts = [0, *starts]

    chunks: list[ChunkRecord] = []
    for position, start_index in enumerate(starts):
        end_index = starts[position + 1] - 1 if position + 1 < len(starts) else len(lines) - 1
        chunks.append(
            make_chunk(
                relative_path,
                start_line=start_index + 1,
                end_line=end_index + 1,
                lines=lines[start_index : end_index + 1],
            )
        )
    return chunks


def chunk_line_ranges(
    relative_path: Path,
    content: str,
    *,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    overlap_lines: int = DEFAULT_CHUNK_OVERLAP_LINES,
) -> list[ChunkRecord]:
    if chunk_lines <= 0:
        raise ValueError("chunk_lines must be positive")
    if overlap_lines < 0 or overlap_lines >= chunk_lines:
        raise ValueError("overlap_lines must be non-negative and smaller than chunk_lines")

    lines = split_lines(content)
    if not lines:
        return []

    chunks: list[ChunkRecord] = []
    start_index = 0
    while start_index < len(lines):
        end_index = min(start_index + chunk_lines, len(lines))
        chunks.append(
            make_chunk(
                relative_path,
                start_line=start_index + 1,
                end_line=end_index,
                lines=lines[start_index:end_index],
            )
        )
        if end_index == len(lines):
            break
        start_index = end_index - overlap_lines
    return chunks


def make_chunk(
    relative_path: Path,
    *,
    start_line: int,
    end_line: int,
    lines: Iterable[str],
) -> ChunkRecord:
    text = "\n".join(lines)
    return ChunkRecord(
        relative_path=path_to_index_string(relative_path),
        start_line=start_line,
        end_line=end_line,
        text=text,
        content_hash=hash_text(text),
    )


def keyword_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
) -> list[SearchResult]:
    init_schema(connection)
    match_query = build_fts_query(query)
    if not match_query:
        return []

    rows = connection.execute(
        """
        SELECT
            chunks.id AS chunk_id,
            chunks.relative_path,
            chunks.start_line,
            chunks.end_line,
            chunks.text,
            bm25(chunks_fts) AS rank
        FROM chunks_fts
        JOIN chunks ON chunks.id = chunks_fts.chunk_id
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()

    return [
        SearchResult(
            chunk_id=int(row["chunk_id"]),
            relative_path=str(row["relative_path"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            preview=text_preview(str(row["text"])),
            score=float(-row["rank"]),
        )
        for row in rows
    ]


def build_fts_query(query: str) -> str:
    terms = re.findall(r"[\w.-]+", query)
    return " ".join(f'"{term}"' for term in terms)


def text_preview(text: str, *, max_length: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3].rstrip() + "..."


def split_lines(content: str) -> list[str]:
    return content.splitlines() or ([""] if content else [])


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def path_to_index_string(path: Path) -> str:
    return path.as_posix()
