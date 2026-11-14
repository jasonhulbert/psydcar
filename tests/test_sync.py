from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from cloud.sync.service import RequestError, SyncService, digest
from psydcar.api import create_app
from psydcar.core.sync import Sync
from psydcar.core.sync_files import version
from psydcar.core.sync_remote import RemoteError, SyncRemote
from psydcar.store import SidecarStore


class MemoryObjects:
    """Implement the object operations the sync service uses."""

    def __init__(self) -> None:
        self.records: dict[str, tuple[dict, str]] = {}
        self.contents: dict[str, bytes] = {}
        self.revision = 0

    def get(self, key: str) -> tuple[dict, str]:
        if key not in self.records:
            raise RequestError(404, "Sync record unavailable.")
        return copy.deepcopy(self.records[key])

    def put(self, key: str, value: dict, etag: str | None = None) -> str:
        if (self.records[key][1] if key in self.records else None) != etag:
            raise RequestError(409, "The cloud version changed. Sync again.")
        self.revision += 1
        self.records[key] = (copy.deepcopy(value), str(self.revision))
        return str(self.revision)

    def url(self, method: str, key: str, **params) -> str:
        return key

    def verify(self, key: str, size: int) -> None:
        if version(self.contents[key]) != {"digest": key.rsplit("/", 1)[-1], "size": size}:
            raise RequestError(409, "Incomplete upload.")


class InProcessCloud:
    def __init__(self) -> None:
        self.online = True
        self.objects = MemoryObjects()
        self.service = SyncService(self.objects, digest("test-enrollment"))

    def remote(self, endpoint: str, credential: str) -> SyncRemote:
        cloud = self

        class Remote(SyncRemote):
            def call(self, action: str, **payload) -> dict:
                if not cloud.online:
                    raise RemoteError("The cloud is unreachable.")
                try:
                    return cloud.service.dispatch(f"Bearer {self.credential}", {"action": action, **payload})
                except RequestError as exc:
                    raise RemoteError(str(exc), exc.status) from exc

            def upload(self, url: str, data: bytes, blob: dict) -> None:
                cloud.objects.contents.setdefault(url, data)

            def download(self, url: str, blob: dict) -> bytes:
                return cloud.objects.contents[url]

        return Remote(endpoint, credential)


class Device:
    def __init__(self, root: Path, port: int):
        self.source = root / "notes"
        self.source.mkdir(parents=True)
        store = SidecarStore(root / "state.sqlite3")
        sidecar = store.create("Notes", self.source)
        self.base = f"/api/v1/sidecars/{sidecar.id}"
        origin = f"http://127.0.0.1:{port}"
        app = create_app(store, port, watch=False, sync_service=Sync(store, "https://sync.example.test"))
        self.client = TestClient(app, base_url=origin)
        self.headers = {"Origin": origin, "X-Psydcar-Token": self.client.get("/api/v1/session").json()["token"]}

    def sync(self, route: str = "", **body) -> dict:
        return self.client.post(self.base + "/sync" + route, headers=self.headers, json=body).raise_for_status().json()

    def save(self, content: str) -> None:
        url = self.base + "/file?path=notes.md"
        old = self.client.get(url).raise_for_status().json()
        self.client.put(url, headers=self.headers, json={"content": content, "version": old["version"]}).raise_for_status()


class SyncTests(unittest.TestCase):
    def test_pair_edit_offline_and_resolve_in_the_app(self) -> None:
        cloud = InProcessCloud()
        with tempfile.TemporaryDirectory() as directory, patch("psydcar.core.sync.SyncRemote", cloud.remote):
            left = Device(Path(directory) / "left", 8765)
            right = Device(Path(directory) / "right", 8766)
            with left.client, right.client:
                (left.source / "notes.md").write_text("# Shared\n", encoding="utf-8")
                (left.source / "photo.png").write_bytes(b"image fixture")
                left.sync("/enable", enrollment_key="test-enrollment")
                left.sync()
                right.sync("/join", code=left.sync("/pair")["code"])
                right.sync()
                self.assertEqual((right.source / "photo.png").read_bytes(), b"image fixture")

                cloud.online = False
                left.save("# Notes from device A\n")
                right.save("# Notes from device B\n")
                left.sync()

                cloud.online = True
                left.sync()
                item, = right.sync()["conflicts"]
                cloud.online = False
                conflict = right.base + "/sync/conflicts/" + item["id"]
                detail = right.client.get(conflict).raise_for_status().json()
                self.assertEqual(detail["diff"].splitlines()[-2:],
                                 ["-# Notes from device B", "+# Notes from device A"])
                merged = "# Notes from both devices\n"
                right.sync("/conflicts/" + item["id"], decision="merge", local_digest=detail["local"]["digest"], merged=merged)

                cloud.online = True
                right.sync()
                self.assertEqual(left.sync()["conflicts"], [])
                self.assertEqual((left.source / "notes.md").read_text(encoding="utf-8"), merged)
                (right.source / "photo.png").unlink()
                right.sync()
                left.sync()
                self.assertFalse((left.source / "photo.png").exists())
