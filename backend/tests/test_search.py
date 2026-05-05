import pytest

import ksidecar.search as search_module
from ksidecar.index import (
    SearchResult,
    connect_index,
    index_path_for_sidecar,
    rebuild_sidecar_index,
)
from ksidecar.search import SearchError, merge_search_results, search_index
from ksidecar.sidecars import SidecarRegistry
from ksidecar.vectors import VectorIndexError


def test_hybrid_search_includes_keyword_and_semantic_matches(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "exact.md").write_text("# Exact\nneedle api docs\n", encoding="utf-8")
    (source_root / "concept.md").write_text("# Concept\ncallback hooks\n", encoding="utf-8")
    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    rebuild_sidecar_index(registry, "fixture")

    def fake_semantic_search(connection, query, *, storage_dir, embedding_model, limit):
        return [
            SearchResult(
                chunk_id=chunk_id_for_path(connection, "concept.md"),
                relative_path="concept.md",
                start_line=1,
                end_line=2,
                preview="# Concept callback hooks",
                score=0.95,
            )
        ]

    monkeypatch.setattr(search_module, "semantic_search", fake_semantic_search)

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        results = search_index(
            connection,
            "needle",
            storage_dir=registry.storage_dir("fixture"),
            mode="hybrid",
        )

    assert {result.relative_path for result in results} == {"exact.md", "concept.md"}


def test_hybrid_search_merges_duplicate_chunk_ids():
    keyword = SearchResult(1, "same.md", 1, 2, "keyword preview", 10.0)
    semantic = SearchResult(1, "same.md", 1, 2, "semantic preview", 0.7)

    results = merge_search_results([keyword], [semantic])

    assert len(results) == 1
    assert results[0].chunk_id == 1
    assert results[0].preview == "keyword preview"
    assert results[0].score == 2.0


def test_hybrid_ranking_uses_normalized_scores():
    keyword_results = [
        SearchResult(1, "strong-keyword.md", 1, 1, "one", 100.0),
        SearchResult(2, "combined.md", 1, 1, "two", 50.0),
        SearchResult(4, "weak-keyword.md", 1, 1, "four", 1.0),
    ]
    semantic_results = [
        SearchResult(2, "combined.md", 1, 1, "two", 1.0),
        SearchResult(3, "semantic-only.md", 1, 1, "three", 0.5),
    ]

    results = merge_search_results(keyword_results, semantic_results, limit=2)

    assert [result.chunk_id for result in results] == [2, 1]
    assert results[0].score > results[1].score
    assert results[1].score == 1.0


def test_search_index_rejects_invalid_mode_and_limit(tmp_path):
    with connect_index(tmp_path / "index.sqlite") as connection:
        with pytest.raises(SearchError, match="unsupported search mode"):
            search_index(connection, "needle", storage_dir=tmp_path, mode="invalid")

        with pytest.raises(SearchError, match="search limit must be positive"):
            search_index(connection, "needle", storage_dir=tmp_path, limit=0)


def test_semantic_search_reports_vector_runtime_failures(monkeypatch, tmp_path):
    def fail_semantic_search(connection, query, *, storage_dir, embedding_model, limit):
        raise VectorIndexError("semantic dependencies are missing")

    monkeypatch.setattr(search_module, "semantic_search", fail_semantic_search)

    with connect_index(tmp_path / "index.sqlite") as connection:
        with pytest.raises(SearchError, match="semantic dependencies are missing"):
            search_index(connection, "needle", storage_dir=tmp_path, mode="semantic")


def chunk_id_for_path(connection, relative_path):
    row = connection.execute(
        "SELECT id FROM chunks WHERE relative_path = ?",
        (relative_path,),
    ).fetchone()
    assert row is not None
    return int(row["id"])
