from pathlib import Path

from psydecar.config import AppConfig
from psydecar.paths import app_storage_root, ensure_app_storage_root, sidecars_root


def test_app_storage_root_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("PSYDECAR_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert app_storage_root() == tmp_path / ".psydecar"


def test_app_storage_root_can_be_overridden(monkeypatch, tmp_path):
    custom_root = tmp_path / "custom-storage"
    monkeypatch.setenv("PSYDECAR_HOME", str(custom_root))

    assert app_storage_root() == custom_root


def test_ensure_app_storage_root_creates_expected_layout(tmp_path):
    root = ensure_app_storage_root(tmp_path / "storage")

    assert root.is_dir()
    assert sidecars_root(root).is_dir()


def test_app_config_loads_runtime_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("PSYDECAR_HOME", str(tmp_path / "storage"))
    monkeypatch.setenv("PSYDECAR_MAX_FILE_SIZE_BYTES", "42")
    monkeypatch.setenv("PSYDECAR_IGNORED_DIRS", ".git,node_modules,custom")
    monkeypatch.setenv("PSYDECAR_EMBEDDING_MODEL", "local/test-model")

    config = AppConfig.load()

    assert config.storage_root == tmp_path / "storage"
    assert config.max_file_size_bytes == 42
    assert config.ignored_directories == (".git", "node_modules", "custom")
    assert config.embedding_model == "local/test-model"
