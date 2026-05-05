import sqlite3
from pathlib import Path

from psydecar.index import (
    INDEX_FILENAME,
    chunk_content,
    connect_index,
    index_path_for_sidecar,
    keyword_search,
    list_indexing_errors,
    rebuild_sidecar_index,
    refresh_sidecar_index,
)
from psydecar.sidecars import SidecarRegistry


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


def test_refresh_replaces_only_modified_file_chunks(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    keep_path = source_root / "keep.md"
    edit_path = source_root / "edit.md"
    keep_path.write_text("# Keep\nsteady needle\n", encoding="utf-8")
    edit_path.write_text("# Edit\nold needle\n", encoding="utf-8")

    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    rebuild_sidecar_index(registry, "fixture")

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        before = {
            row["relative_path"]: row["id"]
            for row in connection.execute(
                "SELECT id, relative_path FROM chunks ORDER BY relative_path"
            )
        }

    edit_path.write_text("# Edit\nnew phrase with extra text\n", encoding="utf-8")
    result = refresh_sidecar_index(registry, "fixture")

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        after = {
            row["relative_path"]: (row["id"], row["text"])
            for row in connection.execute(
                "SELECT id, relative_path, text FROM chunks ORDER BY relative_path"
            )
        }

    assert result.new_count == 0
    assert result.modified_count == 1
    assert result.unchanged_count == 1
    assert result.deleted_count == 0
    assert after["keep.md"][0] == before["keep.md"]
    assert after["edit.md"][0] != before["edit.md"]
    assert after["edit.md"][1] == "# Edit\nnew phrase with extra text"


def test_refresh_removes_deleted_file_from_documents_chunks_and_fts(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    deleted_path = source_root / "delete.md"
    deleted_path.write_text("# Delete\nneedle\n", encoding="utf-8")
    (source_root / "keep.md").write_text("# Keep\nneedle\n", encoding="utf-8")

    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    rebuild_sidecar_index(registry, "fixture")

    deleted_path.unlink()
    result = refresh_sidecar_index(registry, "fixture")

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        document_paths = [
            row["relative_path"]
            for row in connection.execute("SELECT relative_path FROM documents")
        ]
        chunk_paths = [
            row["relative_path"]
            for row in connection.execute("SELECT relative_path FROM chunks")
        ]
        fts_paths = [
            row["relative_path"]
            for row in connection.execute("SELECT relative_path FROM chunks_fts")
        ]

    assert result.deleted_count == 1
    assert document_paths == ["keep.md"]
    assert chunk_paths == ["keep.md"]
    assert fts_paths == ["keep.md"]


def test_refresh_indexes_new_file_and_updates_sidecar_status_counts(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "first.md").write_text("# First\nneedle\n", encoding="utf-8")

    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    rebuild_sidecar_index(registry, "fixture")
    (source_root / "second.md").write_text("# Second\nneedle\n", encoding="utf-8")

    result = refresh_sidecar_index(registry, "fixture")
    sidecar = registry.get("fixture")

    assert result.new_count == 1
    assert result.document_count == 2
    assert result.chunk_count == 2
    assert sidecar.indexing_status == "indexed"
    assert sidecar.last_refresh_at is not None
    assert sidecar.indexed_file_count == 2
    assert sidecar.chunk_count == 2
    assert sidecar.error_count == 0


def test_refresh_captures_file_error_without_failing_whole_refresh(
    monkeypatch,
    tmp_path,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "ok.md").write_text("# Ok\nneedle\n", encoding="utf-8")
    (source_root / "bad.md").write_text("# Bad\nneedle\n", encoding="utf-8")

    registry = SidecarRegistry(tmp_path / "storage")
    registry.create(source_root, sidecar_id="fixture")
    rebuild_sidecar_index(registry, "fixture")

    import psydecar.index as index_module

    original_read_text_content = index_module.read_text_content

    def fail_bad_file(path: Path) -> str:
        if path.name == "bad.md":
            raise OSError("cannot read fixture")
        return original_read_text_content(path)

    monkeypatch.setattr(index_module, "read_text_content", fail_bad_file)
    (source_root / "bad.md").write_text("# Bad\nchanged\n", encoding="utf-8")
    (source_root / "ok.md").write_text("# Ok\nchanged\n", encoding="utf-8")

    result = refresh_sidecar_index(registry, "fixture")

    with connect_index(index_path_for_sidecar(registry, "fixture")) as connection:
        errors = list_indexing_errors(connection)
        ok_chunks = keyword_search(connection, "changed")

    assert result.modified_count == 1
    assert result.error_count == 1
    assert [(error.relative_path, error.stage, error.message) for error in errors] == [
        ("bad.md", "refresh", "cannot read fixture")
    ]
    assert {result.relative_path for result in ok_chunks} == {"ok.md"}
