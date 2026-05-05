"""LanceDB-backed semantic search index."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

from psydecar.defaults import DEFAULT_EMBEDDING_MODEL
from psydecar.index import SearchResult, text_preview

VECTOR_TABLE_NAME = "chunks"
VECTOR_DIRNAME = "vectors.lance"


class VectorIndexError(RuntimeError):
    """Raised when semantic indexing or search cannot run locally."""


@dataclass(frozen=True)
class VectorChunk:
    chunk_id: int
    relative_path: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


@dataclass(frozen=True)
class VectorSearchHit:
    chunk_id: int
    content_hash: str
    score: float


class EmbeddingService(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def replace_all(self, rows: Sequence[dict[str, Any]]) -> None: ...

    def search(self, vector: Sequence[float], *, limit: int) -> list[VectorSearchHit]: ...


class LocalEmbeddingService:
    """Lazy local sentence-transformers embedding service."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.model_name = model_name
        self._model: Any | None = None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        try:
            embeddings = model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except TypeError:
            embeddings = model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, vector)) for vector in embeddings]

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            sentence_transformers = import_module("sentence_transformers")
        except ImportError as exc:
            raise VectorIndexError(
                "semantic search requires the local 'sentence-transformers' package "
                f"and model '{self.model_name}'. Install local semantic dependencies with "
                "`uv sync --extra semantic --group dev`."
            ) from exc
        try:
            self._model = sentence_transformers.SentenceTransformer(self.model_name)
        except Exception as exc:  # noqa: BLE001 - expose model loading failures clearly.
            raise VectorIndexError(
                f"could not load local embedding model '{self.model_name}': {exc}. "
                "Confirm the model is available locally or set PSYDECAR_EMBEDDING_MODEL."
            ) from exc
        return self._model


class LanceVectorStore:
    """Thin wrapper around a LanceDB sidecar table."""

    def __init__(self, vector_dir: Path, table_name: str = VECTOR_TABLE_NAME) -> None:
        self.vector_dir = vector_dir
        self.table_name = table_name

    def replace_all(self, rows: Sequence[dict[str, Any]]) -> None:
        try:
            lancedb = import_module("lancedb")
        except ImportError as exc:
            raise VectorIndexError("semantic search requires the local 'lancedb' package") from exc

        self.vector_dir.mkdir(parents=True, exist_ok=True)
        database = lancedb.connect(self.vector_dir)
        if rows:
            database.create_table(self.table_name, data=list(rows), mode="overwrite")
        elif self.table_name in database.table_names():
            database.drop_table(self.table_name)

    def search(self, vector: Sequence[float], *, limit: int) -> list[VectorSearchHit]:
        try:
            lancedb = import_module("lancedb")
        except ImportError as exc:
            raise VectorIndexError("semantic search requires the local 'lancedb' package") from exc

        database = lancedb.connect(self.vector_dir)
        if self.table_name not in database.table_names():
            return []

        table = database.open_table(self.table_name)
        rows = table.search(list(vector)).limit(limit).to_list()
        return [
            VectorSearchHit(
                chunk_id=int(row["chunk_id"]),
                content_hash=str(row["content_hash"]),
                score=score_from_distance(row.get("_distance")),
            )
            for row in rows
        ]


def vector_path_for_sidecar(storage_dir: Path) -> Path:
    return storage_dir / VECTOR_DIRNAME


def vector_runtime_available() -> bool:
    return find_spec("lancedb") is not None and find_spec("sentence_transformers") is not None


def sync_vector_index(
    chunks: Sequence[VectorChunk],
    *,
    storage_dir: Path,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    embedding_service: EmbeddingService | None = None,
    vector_store: VectorStore | None = None,
) -> None:
    """Rebuild the LanceDB vector table from the current SQLite chunk set."""

    embeddings = (embedding_service or LocalEmbeddingService(embedding_model)).embed(
        [chunk.text for chunk in chunks]
    )
    rows = [
        {
            "chunk_id": chunk.chunk_id,
            "relative_path": chunk.relative_path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "text": chunk.text,
            "content_hash": chunk.content_hash,
            "vector": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    (vector_store or LanceVectorStore(vector_path_for_sidecar(storage_dir))).replace_all(rows)


def search_vectors(
    query: str,
    *,
    chunks: Iterable[VectorChunk],
    storage_dir: Path,
    limit: int = 10,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    embedding_service: EmbeddingService | None = None,
    vector_store: VectorStore | None = None,
) -> list[SearchResult]:
    if not query.strip():
        return []

    query_vector = (embedding_service or LocalEmbeddingService(embedding_model)).embed([query])[0]
    hits = (vector_store or LanceVectorStore(vector_path_for_sidecar(storage_dir))).search(
        query_vector,
        limit=limit,
    )
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    results: list[SearchResult] = []
    for hit in hits:
        chunk = chunks_by_id.get(hit.chunk_id)
        if chunk is None or chunk.content_hash != hit.content_hash:
            continue
        results.append(
            SearchResult(
                chunk_id=chunk.chunk_id,
                relative_path=chunk.relative_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                preview=text_preview(chunk.text),
                score=hit.score,
            )
        )
        if len(results) >= limit:
            break
    return results


def score_from_distance(distance: Any) -> float:
    if distance is None:
        return 0.0
    value = float(distance)
    return 1.0 / (1.0 + max(value, 0.0))
