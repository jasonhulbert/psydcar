from fastapi.testclient import TestClient

from ksidecar.api import create_app
from ksidecar.config import AppConfig


def make_client(tmp_path):
    return TestClient(create_app(AppConfig(storage_root=tmp_path / "storage")))


def test_api_create_list_delete_sidecar_for_arbitrary_source_root(tmp_path):
    client = make_client(tmp_path)
    source_root = tmp_path / "source"
    source_root.mkdir()

    create_response = client.post(
        "/api/sidecars",
        json={"id": "docs", "name": "Docs", "source_root": str(source_root)},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["id"] == "docs"
    assert created["name"] == "Docs"
    assert created["root_path"] == str(source_root.resolve())
    assert created["indexing_status"] == "not_indexed"

    list_response = client.get("/api/sidecars")
    assert list_response.status_code == 200
    assert [sidecar["id"] for sidecar in list_response.json()] == ["docs"]

    delete_response = client.delete("/api/sidecars/docs")
    assert delete_response.status_code == 204
    assert client.get("/api/sidecars").json() == []


def test_api_rebuild_refresh_files_errors_mcp_config_and_search(tmp_path):
    client = make_client(tmp_path)
    source_root = tmp_path / "source"
    source_root.mkdir()
    notes_path = source_root / "notes.md"
    notes_path.write_text("# Notes\nneedle api docs\n", encoding="utf-8")
    (source_root / "image.png").write_bytes(b"png")

    assert client.post(
        "/api/sidecars",
        json={"id": "docs", "source_root": str(source_root)},
    ).status_code == 201

    rebuild_response = client.post("/api/sidecars/docs/rebuild")
    assert rebuild_response.status_code == 200
    assert rebuild_response.json() == {
        "sidecar_id": "docs",
        "operation": "rebuild",
        "status": "queued",
    }

    files_response = client.get("/api/sidecars/docs/files")
    assert files_response.status_code == 200
    assert files_response.json()["files"] == [
        {"relative_path": "notes.md", "extension": ".md", "size_bytes": notes_path.stat().st_size}
    ]

    search_response = client.get(
        "/api/sidecars/docs/search",
        params={"q": "needle", "mode": "keyword", "limit": 5},
    )
    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert search_payload["sidecar_id"] == "docs"
    assert search_payload["query"] == "needle"
    assert search_payload["mode"] == "keyword"
    assert search_payload["results"][0]["relative_path"] == "notes.md"

    errors_response = client.get("/api/sidecars/docs/errors")
    assert errors_response.status_code == 200
    assert errors_response.json() == {"sidecar_id": "docs", "errors": []}

    mcp_response = client.get("/api/sidecars/docs/mcp-config")
    assert mcp_response.status_code == 200
    mcp_payload = mcp_response.json()
    assert mcp_payload["command"] == "ksidecar"
    assert mcp_payload["args"] == ["mcp", "--sidecars", "docs"]
    assert mcp_payload["config"]["mcpServers"]["ksidecar-docs"]["args"] == [
        "mcp",
        "--sidecars",
        "docs",
    ]

    notes_path.write_text("# Notes\nneedle api docs\nsecond line\n", encoding="utf-8")
    refresh_response = client.post("/api/sidecars/docs/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["operation"] == "refresh"
    assert client.get("/api/sidecars").json()[0]["indexed_file_count"] == 1


def test_api_validation_and_not_found_failures(tmp_path):
    client = make_client(tmp_path)

    missing_source_response = client.post(
        "/api/sidecars",
        json={"id": "missing", "source_root": str(tmp_path / "missing")},
    )
    assert missing_source_response.status_code == 400
    assert "source root does not exist" in missing_source_response.json()["detail"]

    invalid_body_response = client.post(
        "/api/sidecars",
        json={"source_root": str(tmp_path), "max_file_size_bytes": 0},
    )
    assert invalid_body_response.status_code == 422

    assert client.get("/api/sidecars/missing/files").status_code == 404

    source_root = tmp_path / "source"
    source_root.mkdir()
    assert client.post(
        "/api/sidecars",
        json={"id": "docs", "source_root": str(source_root)},
    ).status_code == 201

    invalid_search_response = client.get(
        "/api/sidecars/docs/search",
        params={"q": "needle", "mode": "invalid"},
    )
    assert invalid_search_response.status_code == 422

    invalid_limit_response = client.get(
        "/api/sidecars/docs/search",
        params={"q": "needle", "limit": 0},
    )
    assert invalid_limit_response.status_code == 422

