import sqlite3
from pathlib import Path

from ksidecar.index import (
    INDEX_FILENAME,
    chunk_content,
    connect_index,
    index_path_for_sidecar,
    keyword_search,
    rebuild_sidecar_index,
)
from ksidecar.sidecars import SidecarRegistry


def test_chunk_markdown_splits_by_headings():
    chunks = chunk_content(
        Path("guide.md"),
        ".md",
        "intro\n# First\nalpha\n## Second\nbeta\n",
    )

    assert [(chunk.start_line, chunk.end_line, chunk.text) for chunk in chunks] == [
        (1, 1, "intro"),
        (2, 3, "# First\nalpha"),
        (4, 5, "## Second\nbeta"),
    ]


def test_chunk_text_uses_line_windows_with_overlap():
    content = "\n".join(f"line {index}" for index in range(1, 151))

    chunks = chunk_content(Path("notes.txt"), ".txt", content)

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 140),
        (121, 150),
    ]


def test_rebuild_indexes_fixture_documents_chunks_and_search(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text(
        "# Alpha\nneedle markdown content\n## Beta\nmore text\n",
        encoding="utf-8",
    )
    (source_root / "app.py").write_text(
        "def run():\n    return 'needle code'\n",
        encoding="utf-8",
    )
    (source_root / "ignored.png").write_bytes(b"png")

    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")

    result = rebuild_sidecar_index(registry, "fixture")

    assert result.document_count == 2
    assert result.chunk_count == 3
    assert result.error_count == 0
    assert index_path_for_sidecar(registry, "fixture") == (
        tmp_path / "storage" / "sidecars" / "fixture" / INDEX_FILENAME
    )

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        documents = connection.execute(
            "SELECT relative_path, extension, status FROM documents ORDER BY relative_path"
        ).fetchall()
        chunks = connection.execute(
            """
            SELECT relative_path, start_line, end_line, text
            FROM chunks
            ORDER BY relative_path, start_line
            """
        ).fetchall()
        results = keyword_search(connection, "needle")

    assert [tuple(row) for row in documents] == [
        ("README.md", ".md", "indexed"),
        ("app.py", ".py", "indexed"),
    ]
    assert [tuple(row) for row in chunks] == [
        ("README.md", 1, 2, "# Alpha\nneedle markdown content"),
        ("README.md", 3, 4, "## Beta\nmore text"),
        ("app.py", 1, 2, "def run():\n    return 'needle code'"),
    ]
    assert {result.relative_path for result in results} == {"README.md", "app.py"}
    assert all(result.chunk_id > 0 for result in results)
    assert all(result.start_line <= result.end_line for result in results)
    assert all(result.preview for result in results)
    assert all(isinstance(result.score, float) for result in results)


def test_rebuild_removes_stale_rows(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    stale_path = source_root / "stale.md"
    stale_path.write_text("# Stale\nold needle\n", encoding="utf-8")

    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    rebuild_sidecar_index(registry, "fixture")

    stale_path.unlink()
    (source_root / "fresh.md").write_text("# Fresh\nnew needle\n", encoding="utf-8")

    result = rebuild_sidecar_index(registry, "fixture")

    with sqlite3.connect(index_path_for_sidecar(registry, "fixture")) as connection:
        paths = [
            row[0]
            for row in connection.execute(
                "SELECT relative_path FROM documents ORDER BY relative_path"
            )
        ]
        fts_paths = [
            row[0]
            for row in connection.execute(
                "SELECT relative_path FROM chunks_fts ORDER BY relative_path"
            )
        ]

    assert result.document_count == 1
    assert paths == ["fresh.md"]
    assert fts_paths == ["fresh.md"]
