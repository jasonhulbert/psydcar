import pytest

from ksidecar.cli import main


def test_help_runs(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0

    output = capsys.readouterr().out
    assert "Manage local Knowledge Sidecar indexes." in output


def test_paths_command_prints_storage_root(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KSIDECAR_HOME", str(tmp_path / "storage"))

    assert main(["paths"]) == 0

    output = capsys.readouterr().out
    assert f"storage_root={tmp_path / 'storage'}" in output
    assert f"sidecars_root={tmp_path / 'storage' / 'sidecars'}" in output
