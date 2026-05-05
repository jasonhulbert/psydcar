from pathlib import Path

from ksidecar.config import AppConfig
from ksidecar.paths import app_storage_root, ensure_app_storage_root, sidecars_root


def test_app_storage_root_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("KSIDECAR_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert app_storage_root() == tmp_path / ".ksidecar"


def test_app_storage_root_can_be_overridden(monkeypatch, tmp_path):
    custom_root = tmp_path / "custom-storage"
    monkeypatch.setenv("KSIDECAR_HOME", str(custom_root))

    assert app_storage_root() == custom_root


def test_ensure_app_storage_root_creates_expected_layout(tmp_path):
    root = ensure_app_storage_root(tmp_path / "storage")

    assert root.is_dir()
    assert sidecars_root(root).is_dir()


def test_app_config_loads_runtime_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("KSIDECAR_HOME", str(tmp_path / "storage"))
    monkeypatch.setenv("KSIDECAR_MAX_FILE_SIZE_BYTES", "42")
    monkeypatch.setenv("KSIDECAR_IGNORED_DIRS", ".git,node_modules,custom")
    monkeypatch.setenv("KSIDECAR_EMBEDDING_MODEL", "local/test-model")

    config = AppConfig.load()

    assert config.storage_root == tmp_path / "storage"
    assert config.max_file_size_bytes == 42
    assert config.ignored_directories == (".git", "node_modules", "custom")
    assert config.embedding_model == "local/test-model"
