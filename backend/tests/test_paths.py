from pathlib import Path

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
