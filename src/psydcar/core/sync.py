"""Reconcile each local file against its last shared version and the cloud."""

from __future__ import annotations

import os
import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from ..evidence import MAX_FILE_BYTES, SUPPORTED_EXTENSIONS
from ..files import FileConflict, text_bytes
from ..store import SidecarStore, StoreError, _now
from ..sync_protocol import ID, MAX_FILES, SECRET, Blob, manifest
from .diff import text_diff
from .storage import exclusive_file_lock
from .sync_files import SyncFiles, limit, version
from .sync_remote import SyncRemote
from .workspace import Workspace


def _digest(blob: Blob | None) -> str | None:
    return blob["digest"] if blob is not None else None


def _text(path: str, data: bytes | None) -> str | None:
    if data is None or Path(path).suffix.lower() not in SUPPORTED_EXTENSIONS or len(data) > MAX_FILE_BYTES or b"\0" in data:
        return None
    try:
        return data.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def _batches(paths: list[str], versions: dict[str, Blob]) -> Iterator[list[str]]:
    """Request short-lived URLs only for the next small group of files."""
    batch: list[str] = []
    size = 0
    for path in paths:
        length = versions[path]["size"] if path in versions else 0
        if batch and (len(batch) == 16 or size + length > 8 * 1024 * 1024):
            yield batch
            batch = []
            size = 0
        batch.append(path)
        size += length
    if batch:
        yield batch


class Sync:
    def __init__(self, store: SidecarStore, endpoint: str | None = None):
        self.store = store
        self.workspace = Workspace(store)
        self.endpoint = endpoint if endpoint is not None else os.getenv("PSYDCAR_SYNC_URL", "")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        try:
            with exclusive_file_lock(self.store.db_path.with_suffix(".sync.lock"), wait=False):
                yield
        except BlockingIOError as exc:
            raise FileConflict("Another sync operation is running. Try again shortly.", None) from exc

    def _identity(self) -> sqlite3.Row | None:
        with self.store._connect() as state:
            return state.execute("SELECT * FROM sync_identity WHERE id = 1").fetchone()

    def _remote(self, account: str | None = None, *, setup: bool = False) -> SyncRemote:
        identity = self._identity()
        if identity is not None:
            same_account = account is None or identity["credential"].split(".", 1)[0] == account
            if same_account and (identity["active"] or setup):
                return SyncRemote(identity["endpoint"], identity["credential"])
            if identity["active"]:
                raise StoreError("This installation is already paired to a different sync account.")
        if not setup or not self.endpoint:
            raise StoreError("The Psydcar sync service address must be configured before pairing.")
        account = account or uuid.uuid4().hex
        credential = f"{account}.{uuid.uuid4().hex}.{secrets.token_urlsafe(32)}"
        remote = SyncRemote(self.endpoint, credential)
        with self.store._connect() as state:
            state.execute("INSERT OR REPLACE INTO sync_identity VALUES (1, ?, ?, 0)", (self.endpoint, credential))
        return remote

    def _link(self, sidecar_id: str, remote_id: str) -> None:
        with self.store.file_writes(), self.store._connect() as state:
            self.store._find(state, sidecar_id, True)
            state.execute("UPDATE sync_identity SET active = 1")
            state.execute("INSERT INTO sync_links (sidecar_id, remote_id) VALUES (?, ?)", (sidecar_id, remote_id))

    def _validate_link(self, sidecar_id: str) -> None:
        local = self.workspace.files(sidecar_id).root
        with self.store._connect() as state:
            rows = state.execute("SELECT id, source_path FROM sidecars JOIN sync_links ON id = sidecar_id").fetchall()
        for row in rows:
            existing = Path(row["source_path"]).resolve()
            if row["id"] == sidecar_id:
                raise StoreError("This sidecar is already set up for sync.")
            if existing.is_relative_to(local) or local.is_relative_to(existing):
                raise StoreError("Linked sidecars must have separate source folders.")

    def enable(self, sidecar_id: str, enrollment_key: str) -> dict[str, Any]:
        with self._lock():
            self._validate_link(sidecar_id)
            identity = self._identity()
            remote = self._remote(setup=True)
            if identity is None or not identity["active"]:
                remote.call("enroll", enrollment_key=enrollment_key)
            remote_id = uuid.UUID(sidecar_id).hex
            remote.call("create", sidecar=remote_id)
            self._link(sidecar_id, remote_id)
        return self.status(sidecar_id)

    def join(self, sidecar_id: str, pairing_code: str) -> dict[str, Any]:
        account, _, code = pairing_code.strip().partition(".")
        if not ID.fullmatch(account) or not SECRET.fullmatch(code):
            raise StoreError("Enter the full pairing code shown on the other device.")
        with self._lock():
            self._validate_link(sidecar_id)
            remote = self._remote(account, setup=True)
            remote_id = remote.call("join", code=code).get("sidecar")
            if not isinstance(remote_id, str) or not ID.fullmatch(remote_id):
                raise StoreError("The sync service returned an invalid sidecar identifier.")
            with self.store._connect() as state:
                if state.execute("SELECT 1 FROM sync_links WHERE remote_id = ?", (remote_id,)).fetchone():
                    raise StoreError("This cloud sidecar is already linked to another folder on this device.")
            self._link(sidecar_id, remote_id)
        return self.status(sidecar_id)

    def _remote_id(self, sidecar_id: str) -> str:
        with self.store._connect() as state:
            link = state.execute("SELECT remote_id FROM sync_links WHERE sidecar_id = ?", (sidecar_id,)).fetchone()
        if link is None:
            raise StoreError("Set up sync for this sidecar first.")
        return link["remote_id"]

    def pair(self, sidecar_id: str) -> str:
        with self._lock():
            return self._remote().call("pair", sidecar=self._remote_id(sidecar_id))["code"]

    def revoke(self, sidecar_id: str) -> None:
        with self._lock():
            self._remote_id(sidecar_id)
            self._remote().call("revoke")

    def status(self, sidecar_id: str) -> dict[str, Any]:
        with self.store._connect() as state:
            self.store._find(state, sidecar_id, True)
            link = state.execute("SELECT * FROM sync_links WHERE sidecar_id = ?", (sidecar_id,)).fetchone()
            active = state.execute("SELECT 1 FROM sync_identity WHERE active = 1").fetchone() is not None
            conflicts = [dict(row) for row in state.execute(
                "SELECT conflict_id AS id, path FROM sync_paths WHERE sidecar_id = ? AND conflict_id IS NOT NULL ORDER BY path",
                (sidecar_id,),
            )]
        return {
            "available": bool(self.endpoint) or active, "account_active": active, "linked": link is not None,
            "last_synced_at": link["last_synced_at"] if link else None, "error": link["error"] if link else None,
            "revision": link["revision"] if link else 0, "conflicts": conflicts,
        }

    @staticmethod
    def _remember(state: sqlite3.Connection, sidecar_id: str, path: str, shared: str | None) -> None:
        if shared is None:
            state.execute("DELETE FROM sync_paths WHERE sidecar_id = ? AND path = ?", (sidecar_id, path))
        else:
            state.execute("""
                INSERT INTO sync_paths (sidecar_id, path, base_digest)
                SELECT sidecar_id, ?, ? FROM sync_links WHERE sidecar_id = ?
                ON CONFLICT(sidecar_id, path) DO UPDATE SET
                    base_digest = excluded.base_digest, conflict_id = NULL, cloud_digest = NULL, cloud_content = NULL
            """, (path, shared, sidecar_id))

    def run(self, sidecar_id: str) -> dict[str, Any]:
        with self._lock():
            remote_id = self._remote_id(sidecar_id)
            try:
                self._reconcile(sidecar_id, remote_id, self._remote())
            except StoreError as exc:
                with self.store._connect() as state:
                    state.execute("UPDATE sync_links SET error = ? WHERE sidecar_id = ?", (str(exc), sidecar_id))
            else:
                with self.store._connect() as state:
                    state.execute("UPDATE sync_links SET error = NULL, last_synced_at = ? WHERE sidecar_id = ?",
                                  (_now(), sidecar_id))
        return self.status(sidecar_id)

    def _reconcile(self, sidecar_id: str, remote_id: str, remote: SyncRemote) -> None:
        cloud, etag = remote.snapshot(remote_id)
        source = SyncFiles(self.workspace.files(sidecar_id))
        with self.store._connect() as state:
            tracked = {row["path"]: row for row in state.execute(
                "SELECT path, base_digest, conflict_id, cloud_digest FROM sync_paths WHERE sidecar_id = ?", (sidecar_id,),
            )}
        paths = set(source.paths())
        if len(paths) > MAX_FILES:
            raise StoreError("A synced sidecar can have up to 5,000 files.")
        local: dict[str, Blob] = {}
        unavailable: dict[str, str] = {}
        for path in sorted(paths | cloud.keys() | tracked.keys()):
            try:
                if path in cloud and cloud[path]["size"] > limit(path):
                    raise StoreError(f"{path} exceeds the local sync size limit.")
                data = source.read(path)
                if data is not None:
                    local[path] = version(data)
            except StoreError as exc:
                unavailable[path] = str(exc)
        try:
            manifest(local)
        except ValueError as exc:
            raise StoreError(str(exc)) from exc

        incoming: list[str] = []
        outgoing: list[str] = []
        conflicts: list[str] = []
        with self.store._connect() as state:
            for path in sorted((local.keys() | cloud.keys() | tracked.keys()) - unavailable.keys()):
                before = tracked.get(path)
                base = before["base_digest"] if before else None
                here, there = _digest(local.get(path)), _digest(cloud.get(path))
                if here == there:
                    if base != here or before and before["conflict_id"]:
                        self._remember(state, sidecar_id, path, here)
                elif here == base:
                    incoming.append(path)
                elif there == base:
                    outgoing.append(path)
                elif not before or not before["conflict_id"] or before["cloud_digest"] != there:
                    conflicts.append(path)

        # Apply deletions first so ordinary moves and case-only renames converge.
        pulls = sorted(incoming, key=lambda path: path in cloud) + conflicts
        incoming_set = set(incoming)
        for batch in _batches(pulls, cloud):
            downloads = [cloud[path] for path in batch if path in cloud]
            urls = remote.urls(remote_id, "download", downloads) if downloads else {}
            for path in batch:
                theirs = cloud.get(path)
                data = self._download(remote, path, theirs, urls)
                if path in incoming_set:
                    with self.workspace.edit(sidecar_id) as files, self.store._connect() as state:
                        SyncFiles(files).apply(path, local.get(path), data)
                        self._remember(state, sidecar_id, path, _digest(theirs))
                        state.execute("UPDATE sync_links SET revision = revision + 1 WHERE sidecar_id = ?", (sidecar_id,))
                else:
                    with self.store._connect() as state:
                        state.execute("""
                            INSERT INTO sync_paths
                            SELECT sidecar_id, ?, ?, ?, ?, ? FROM sync_links WHERE sidecar_id = ?
                            ON CONFLICT(sidecar_id, path) DO UPDATE SET conflict_id = excluded.conflict_id,
                                cloud_digest = excluded.cloud_digest, cloud_content = excluded.cloud_content
                        """, (path, tracked[path]["base_digest"] if path in tracked else None,
                              uuid.uuid4().hex, _digest(theirs), data, sidecar_id))

        outgoing.sort(key=lambda path: path in local)
        for batch in _batches(outgoing, local):
            for path in batch:
                if path in local:
                    cloud[path] = local[path]
                else:
                    cloud.pop(path, None)
            try:
                manifest(cloud)
            except ValueError as exc:
                raise StoreError(f"Cannot combine the cloud and local paths. {exc}") from exc
            uploads = [local[path] for path in batch if path in local]
            urls = remote.urls(remote_id, "upload", uploads) if uploads else {}
            for path in batch:
                expected = local.get(path)
                data = source.read(path)
                if expected != (version(data) if data is not None else None):
                    raise StoreError(f"{path} changed during sync. Try again.")
                if data is not None and expected is not None:
                    remote.upload(urls[expected["digest"]], data, expected)
            etag = remote.call("commit", sidecar=remote_id, etag=etag, files=cloud).get("etag")
            if not isinstance(etag, str) or not etag:
                raise StoreError("The sync service did not confirm the committed version. Sync again.")
            with self.store._connect() as state:
                for path in batch:
                    self._remember(state, sidecar_id, path, _digest(local.get(path)))
        if unavailable:
            raise StoreError(next(iter(unavailable.values())))

    @staticmethod
    def _download(remote: SyncRemote, path: str, blob: Blob | None, urls: dict[str, str]) -> bytes | None:
        if blob is None:
            return None
        if blob["size"] > limit(path):
            raise StoreError(f"{path} exceeds the local sync size limit.")
        data = remote.download(urls[blob["digest"]], blob)
        if version(data) != blob:
            raise StoreError(f"{path} failed its download integrity check. Sync again.")
        return data

    @staticmethod
    def _conflict(state: sqlite3.Connection, sidecar_id: str, conflict_id: str) -> sqlite3.Row:
        row = state.execute("SELECT * FROM sync_paths WHERE sidecar_id = ? AND conflict_id = ?",
                            (sidecar_id, conflict_id)).fetchone()
        if row is None:
            raise FileConflict("The conflict changed. Refresh the review.", None)
        return row

    def conflict(self, sidecar_id: str, conflict_id: str) -> dict[str, Any]:
        with self.store._connect() as state:
            row = self._conflict(state, sidecar_id, conflict_id)
        path = row["path"]
        here = SyncFiles(self.workspace.files(sidecar_id)).read(path)
        cloud = row["cloud_content"]
        local_text, cloud_text = _text(path, here), _text(path, cloud)
        comparable = (here is None or local_text is not None) and (cloud is None or cloud_text is not None)
        return {
            "id": conflict_id, "path": path, "local": self._review_version(path, here),
            "cloud": self._review_version(path, cloud),
            "diff": text_diff(local_text or "", cloud_text or "") if comparable else None,
        }

    @staticmethod
    def _review_version(path: str, data: bytes | None) -> dict[str, Any] | None:
        return {**version(data), "text": _text(path, data)} if data is not None else None

    def conflict_bytes(self, sidecar_id: str, conflict_id: str, copy: Literal["local", "cloud"],
                       expected: str) -> tuple[str, bytes]:
        with self.store._connect() as state:
            row = self._conflict(state, sidecar_id, conflict_id)
        path = row["path"]
        data = row["cloud_content"] if copy == "cloud" else SyncFiles(self.workspace.files(sidecar_id)).read(path)
        if data is None or version(data)["digest"] != expected:
            raise FileConflict("The file changed. Refresh the review.", None)
        return Path(path).name, data

    def resolve(self, sidecar_id: str, conflict_id: str, local_digest: str | None,
                decision: Literal["local", "cloud", "merge"], merged: str | None = None) -> dict[str, Any]:
        with self._lock(), self.workspace.edit(sidecar_id) as files, self.store._connect() as state:
            row = self._conflict(state, sidecar_id, conflict_id)
            path = row["path"]
            source = SyncFiles(files)
            here = source.read(path)
            expected = version(here) if here is not None else None
            if _digest(expected) != local_digest:
                raise FileConflict("The file changed. Refresh the review before resolving it.", _digest(expected))
            theirs = row["cloud_content"]
            if decision != "local":
                data = theirs
                if decision == "merge":
                    style = here if here is not None else theirs
                    if style is None or merged is None or "\0" in merged or any(
                        content is not None and _text(path, content) is None for content in (here, theirs)
                    ):
                        raise StoreError("Only supported UTF-8 text files can be merged.")
                    data = text_bytes(merged, "\r\n" if b"\r\n" in style else "\n", style.startswith(b"\xef\xbb\xbf"))
                source.apply(path, expected, data)
                state.execute("UPDATE sync_links SET revision = revision + 1 WHERE sidecar_id = ?", (sidecar_id,))
            self._remember(state, sidecar_id, path, row["cloud_digest"])
        return self.status(sidecar_id)
