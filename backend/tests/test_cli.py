import json

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


def test_help_includes_mcp_command(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["mcp", "--help"])

    assert exc_info.value.code == 0
    assert "Start a read-only MCP server over stdio." in capsys.readouterr().out


def test_sidecar_create_list_and_delete_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KSIDECAR_HOME", str(tmp_path / "storage"))
    source_root = tmp_path / "source"
    source_root.mkdir()

    assert (
        main(["sidecar", "create", str(source_root), "--id", "docs", "--name", "Docs", "--json"])
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["id"] == "docs"
    assert created["name"] == "Docs"
    assert created["root_path"] == str(source_root.resolve())

    assert main(["sidecar", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [sidecar["id"] for sidecar in listed] == ["docs"]

    assert main(["sidecar", "delete", "docs", "--json"]) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted == {"deleted": "docs"}

    assert main(["sidecar", "list"]) == 0
    assert "No sidecars registered." in capsys.readouterr().out


def test_rebuild_search_refresh_and_status_from_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KSIDECAR_HOME", str(tmp_path / "storage"))
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "notes.md").write_text("# Notes\nneedle api docs\n", encoding="utf-8")

    assert main(["sidecar", "create", str(source_root), "--id", "docs"]) == 0
    capsys.readouterr()

    assert main(["rebuild", "docs", "--json"]) == 0
    rebuild = json.loads(capsys.readouterr().out)
    assert rebuild == {"chunk_count": 1, "document_count": 1, "error_count": 0}

    assert main(["search", "docs", "needle", "--mode", "keyword", "--json"]) == 0
    results = json.loads(capsys.readouterr().out)
    assert results[0]["relative_path"] == "notes.md"
    assert "needle api docs" in results[0]["preview"]

    (source_root / "notes.md").write_text("# Notes\nneedle api docs\nnew line\n", encoding="utf-8")
    assert main(["refresh", "docs", "--json"]) == 0
    refresh = json.loads(capsys.readouterr().out)
    assert refresh["document_count"] == 1
    assert refresh["modified_count"] == 1

    assert main(["status", "docs", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["sidecar"]["id"] == "docs"
    assert status["sidecar"]["indexing_status"] == "indexed"
    assert status["errors"] == []
