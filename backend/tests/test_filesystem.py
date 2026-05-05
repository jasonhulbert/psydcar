import pytest

from psydecar.filesystem import PathSafetyError, resolve_root_relative_path


def test_resolve_root_relative_path_uses_sidecar_source_root(tmp_path):
    storage_root = tmp_path / "storage" / "sidecars" / "docs"
    storage_root.mkdir(parents=True)
    source_root = tmp_path / "source"
    source_root.mkdir()
    target = source_root / "notes.md"
    target.write_text("# Notes\n", encoding="utf-8")

    assert resolve_root_relative_path(source_root, "notes.md") == target.resolve()


def test_resolve_root_relative_path_rejects_parent_traversal(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()

    with pytest.raises(PathSafetyError):
        resolve_root_relative_path(source_root, "../secret.txt")


def test_resolve_root_relative_path_rejects_absolute_paths(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    with pytest.raises(PathSafetyError):
        resolve_root_relative_path(source_root, outside)


def test_resolve_root_relative_path_rejects_symlink_escape(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    (source_root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError):
        resolve_root_relative_path(source_root, "link/secret.txt")
