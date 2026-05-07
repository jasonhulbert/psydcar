import psydcar.vectors as vector_module
from psydcar.index import (
    connect_index,
    index_path_for_sidecar,
    list_indexing_errors,
    rebuild_sidecar_index,
    select_count,
)
from psydcar.sidecars import SidecarRegistry
from psydcar.vectors import (
    VectorChunk,
    VectorIndexError,
    VectorSearchHit,
    search_vectors,
    sync_vector_index,
    vector_path_for_sidecar,
)


class FakeEmbeddingService:
    def embed(self, texts):
        return [[score_text(text)] for text in texts]


class FakeVectorStore:
    def __init__(self):
        self.rows = []

    def replace_all(self, rows):
        self.rows = list(rows)

    def search(self, vector, *, limit):
        query_score = vector[0]
        ranked = sorted(
            self.rows,
            key=lambda row: abs(row["vector"][0] - query_score),
        )
        return [
            VectorSearchHit(
                chunk_id=row["chunk_id"],
                content_hash=row["content_hash"],
                score=1.0 / (1.0 + abs(row["vector"][0] - query_score)),
            )
            for row in ranked[:limit]
        ]


def test_sync_vector_index_replaces_rows_linked_to_chunk_ids_and_hashes(tmp_path):
    store = FakeVectorStore()
    chunks = [
        make_vector_chunk(1, "alpha.md", "alpha content", "hash-alpha"),
        make_vector_chunk(2, "beta.md", "beta content", "hash-beta"),
    ]

    sync_vector_index(
        chunks,
        storage_dir=tmp_path,
        embedding_service=FakeEmbeddingService(),
        vector_store=store,
    )

    assert [(row["chunk_id"], row["content_hash"], row["relative_path"]) for row in store.rows] == [
        (1, "hash-alpha", "alpha.md"),
        (2, "hash-beta", "beta.md"),
    ]
    assert all(isinstance(row["vector"][0], float) for row in store.rows)

    sync_vector_index(
        [make_vector_chunk(1, "alpha.md", "updated semantic text", "hash-updated")],
        storage_dir=tmp_path,
        embedding_service=FakeEmbeddingService(),
        vector_store=store,
    )

    assert [(row["chunk_id"], row["content_hash"]) for row in store.rows] == [
        (1, "hash-updated")
    ]


def test_semantic_search_returns_keyword_search_result_shape(tmp_path):
    store = FakeVectorStore()
    chunks = [
        make_vector_chunk(1, "invoice.md", "billing invoice payment", "hash-invoice"),
        make_vector_chunk(2, "recipe.md", "basil tomato dinner", "hash-recipe"),
    ]
    sync_vector_index(
        chunks,
        storage_dir=tmp_path,
        embedding_service=FakeEmbeddingService(),
        vector_store=store,
    )

    results = search_vectors(
        "payment invoice",
        chunks=chunks,
        storage_dir=tmp_path,
        embedding_service=FakeEmbeddingService(),
        vector_store=store,
    )

    assert len(results) == 2
    assert results[0].chunk_id == 1
    assert results[0].relative_path == "invoice.md"
    assert results[0].start_line == 1
    assert results[0].end_line == 2
    assert results[0].preview == "billing invoice payment"
    assert isinstance(results[0].score, float)


def test_rebuild_invokes_vector_sync_for_current_sqlite_chunks(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("# Alpha\nsemantic content\n", encoding="utf-8")
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    captured = {}

    def fake_sync_vector_index(chunks, *, storage_dir, embedding_model):
        captured["chunks"] = chunks
        captured["storage_dir"] = storage_dir
        captured["embedding_model"] = embedding_model

    monkeypatch.setattr(vector_module, "vector_runtime_available", lambda: True)
    monkeypatch.setattr(vector_module, "sync_vector_index", fake_sync_vector_index)

    result = rebuild_sidecar_index(registry, "fixture")

    assert result.error_count == 0
    assert captured["storage_dir"] == tmp_path / "storage" / "sidecars" / "fixture"
    assert captured["embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert [(chunk.relative_path, chunk.text) for chunk in captured["chunks"]] == [
        ("README.md", "# Alpha\nsemantic content")
    ]


def test_vector_model_failure_is_recorded_without_corrupting_sqlite(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("# Alpha\nsemantic content\n", encoding="utf-8")
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")

    def fail_sync_vector_index(chunks, *, storage_dir, embedding_model):
        raise VectorIndexError("could not load local embedding model")

    monkeypatch.setattr(vector_module, "vector_runtime_available", lambda: True)
    monkeypatch.setattr(vector_module, "sync_vector_index", fail_sync_vector_index)

    result = rebuild_sidecar_index(registry, "fixture")

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        errors = list_indexing_errors(connection)
        document_count = select_count(connection, "SELECT COUNT(*) FROM documents")
        chunk_count = select_count(connection, "SELECT COUNT(*) FROM chunks")

    assert result.error_count == 1
    assert document_count == 1
    assert chunk_count == 1
    assert [(error.relative_path, error.stage, error.message) for error in errors] == [
        ("__vector__", "vector", "could not load local embedding model")
    ]


def test_vector_path_uses_lancedb_directory_name(tmp_path):
    assert vector_path_for_sidecar(tmp_path) == tmp_path / "vectors.lance"


def make_vector_chunk(
    chunk_id: int,
    relative_path: str,
    text: str,
    content_hash: str,
) -> VectorChunk:
    return VectorChunk(
        chunk_id=chunk_id,
        relative_path=relative_path,
        start_line=1,
        end_line=2,
        text=text,
        content_hash=content_hash,
    )


def score_text(text: str) -> float:
    words = set(text.split())
    if "invoice" in words or "payment" in words or "billing" in words:
        return 1.0
    return 10.0
