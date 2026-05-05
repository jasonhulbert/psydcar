"""Search service that combines keyword and semantic index results."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal

from ksidecar.index import SearchResult, init_schema, keyword_search, semantic_search
from ksidecar.sidecars import SidecarRegistry
from ksidecar.vectors import DEFAULT_EMBEDDING_MODEL, VectorIndexError

SearchMode = Literal["keyword", "semantic", "hybrid"]
DEFAULT_SEARCH_LIMIT = 10


class SearchError(ValueError):
    """Raised when a search request is invalid."""


def search_sidecar(
    registry: SidecarRegistry,
    sidecar_id: str,
    query: str,
    *,
    mode: str = "hybrid",
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[SearchResult]:
    """Search a persisted sidecar using the requested mode."""

    from ksidecar.index import connect_index, index_path_for_sidecar

    sidecar = registry.get(sidecar_id)
    with connect_index(index_path_for_sidecar(registry, sidecar.id)) as connection:
        return search_index(
            connection,
            query,
            storage_dir=registry.storage_dir(sidecar.id),
            embedding_model=sidecar.config.embedding_model,
            mode=mode,
            limit=limit,
        )


def search_index(
    connection: sqlite3.Connection,
    query: str,
    *,
    storage_dir: Path,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    mode: str = "hybrid",
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[SearchResult]:
    """Search an open SQLite index and return stable result payloads."""

    init_schema(connection)
    normalized_limit = normalize_limit(limit)
    if not query.strip():
        return []

    if mode == "keyword":
        return keyword_search(connection, query, limit=normalized_limit)
    if mode == "semantic":
        try:
            return semantic_search(
                connection,
                query,
                storage_dir=storage_dir,
                embedding_model=embedding_model,
                limit=normalized_limit,
            )
        except VectorIndexError as exc:
            raise SearchError(str(exc)) from exc
    if mode == "hybrid":
        candidate_limit = max(normalized_limit * 2, normalized_limit)
        keyword_results = keyword_search(connection, query, limit=candidate_limit)
        try:
            semantic_results = semantic_search(
                connection,
                query,
                storage_dir=storage_dir,
                embedding_model=embedding_model,
                limit=candidate_limit,
            )
        except VectorIndexError:
            semantic_results = []
        return merge_search_results(
            keyword_results,
            semantic_results,
            limit=normalized_limit,
        )
    raise SearchError(f"unsupported search mode: {mode}")


def merge_search_results(
    keyword_results: Sequence[SearchResult],
    semantic_results: Sequence[SearchResult],
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[SearchResult]:
    """Normalize, merge, and deduplicate keyword and semantic results."""

    normalized_limit = normalize_limit(limit)
    keyword_scores = normalize_scores(keyword_results)
    semantic_scores = normalize_scores(semantic_results)
    merged: dict[int, SearchResult] = {}
    merged_scores: dict[int, float] = {}

    for result in keyword_results:
        merged[result.chunk_id] = result
        merged_scores[result.chunk_id] = keyword_scores[result.chunk_id]

    for result in semantic_results:
        if result.chunk_id not in merged:
            merged[result.chunk_id] = result
            merged_scores[result.chunk_id] = 0.0
        merged_scores[result.chunk_id] += semantic_scores[result.chunk_id]

    ranked = sorted(
        merged.values(),
        key=lambda result: (
            merged_scores[result.chunk_id],
            keyword_scores.get(result.chunk_id, 0.0),
            semantic_scores.get(result.chunk_id, 0.0),
            -result.chunk_id,
        ),
        reverse=True,
    )
    return [
        replace(result, score=merged_scores[result.chunk_id])
        for result in ranked[:normalized_limit]
    ]


def normalize_scores(results: Sequence[SearchResult]) -> dict[int, float]:
    """Return per-result scores normalized to the range 0.0-1.0."""

    if not results:
        return {}

    raw_scores = [result.score for result in results]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    if max_score == min_score:
        score = 1.0 if max_score > 0 else 0.0
        return {result.chunk_id: score for result in results}

    span = max_score - min_score
    return {
        result.chunk_id: (result.score - min_score) / span
        for result in results
    }


def normalize_limit(limit: int) -> int:
    if limit < 1:
        raise SearchError("search limit must be positive")
    return limit
