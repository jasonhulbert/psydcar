from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from psydcar.api import create_app
from psydcar.store import SidecarStore


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.source = root / "source"
        self.source.mkdir()
        store = SidecarStore(root / "state" / "psydcar.sqlite3")
        sidecar = store.create("Notes", self.source)
        self.base = f"/api/v1/sidecars/{sidecar.id}"
        self.origin = "http://127.0.0.1:8765"
        self.app = create_app(store, 8765, watch=False)
        self.client = TestClient(self.app, base_url=self.origin)
        self.addCleanup(self.client.close)
        token = self.client.get("/api/v1/session").json()["token"]
        self.headers = {"Origin": self.origin, "X-Psydcar-Token": token}

    def test_manage_files_and_folders(self) -> None:
        files = self.base + "/files"
        for path, kind in [("drafts", "folder"), ("drafts/notes.md", "file")]:
            self.assertEqual(self.client.post(files, headers=self.headers, json={"path": path, "type": kind}).status_code, 201)
        original = self.client.get(self.base + "/file", params={"path": "drafts/notes.md"}).json()
        self.client.put(self.base + "/file", params={"path": "drafts/notes.md"}, headers=self.headers,
                        json={"content": "# Notes\n", "version": original["version"]}).raise_for_status()
        self.client.post(files + "/move", headers=self.headers,
                         json={"source": "drafts", "target": "archive"}).raise_for_status()
        self.assertEqual(self.client.get(self.base + "/file", params={"path": "archive/notes.md"}).json()["content"], "# Notes\n")
        for path in ["archive/notes.md", "archive"]:
            self.client.request("DELETE", files, headers=self.headers, json={"path": path}).raise_for_status()
        self.assertEqual(self.client.get(files).json()["entries"], [])

    def test_open_a_built_web_artifact_and_its_module(self) -> None:
        bundle = self.source / "dist"
        bundle.mkdir()
        (bundle / "index.html").write_text('<script type="module" src="./main.mjs"></script>', encoding="utf-8")
        (bundle / "main.mjs").write_text("export const name = 'Psydcar';", encoding="utf-8")
        started = self.client.post(self.base + "/previews", headers=self.headers, json={"path": "dist/index.html"})
        started.raise_for_status()
        entry = started.json()["path"]
        with TestClient(self.app, base_url="http://localhost:8765") as preview:
            self.assertIn('src="./main.mjs"', preview.get(entry).text)
            script = preview.get(entry.rsplit("/", 1)[0] + "/main.mjs", headers={"Origin": "null"})
            self.assertEqual(script.text, "export const name = 'Psydcar';")
            self.assertEqual(script.headers["access-control-allow-origin"], "null")
