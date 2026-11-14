"""Device authorization and sync operations independent of the S3 adapter."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Protocol

from psydcar.sync_protocol import ID, SECRET, blob, checksum, manifest


class ObjectStore(Protocol):
    def get(self, key: str) -> tuple[dict, str]: ...
    def put(self, key: str, value: dict, etag: str | None = None) -> str: ...
    def url(self, method: str, key: str, **params) -> str: ...
    def verify(self, key: str, size: int) -> None: ...


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def checked(pattern, value: object) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("Invalid sync identifier.")
    return value


def split(value: object, *patterns) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("Invalid sync credential.")
    return tuple(checked(pattern, item) for pattern, item in zip(patterns, value.split("."), strict=True))


class SyncService:
    def __init__(self, storage: ObjectStore, enrollment_digest: str):
        self.storage = storage
        self.enrollment_digest = enrollment_digest

    def dispatch(self, authorization: str, data: dict) -> dict:
        action = data.get("action")
        try:
            account, device, secret = split(authorization.removeprefix("Bearer "), ID, ID, SECRET)
            if not authorization.startswith("Bearer "):
                raise ValueError
        except ValueError as exc:
            raise RequestError(401, "A paired device credential is required.") from exc
        account_key = f"sync/{account}/devices.json"
        if action == "enroll":
            invite = data.get("enrollment_key")
            if not isinstance(invite, str) or len(invite) > 256 or not hmac.compare_digest(
                digest(invite), self.enrollment_digest,
            ):
                raise RequestError(403, "The enrollment key is invalid.")
            try:
                self.storage.put(account_key, {"devices": {device: digest(secret)}, "pair": None})
                return {}
            except RequestError as exc:
                if exc.status != 409:
                    raise
                # A retry after the first response was interrupted.
                record, _ = self.storage.get(account_key)
                self.authenticate(record, device, secret)
                return {}

        try:
            record, account_etag = self.storage.get(account_key)
        except RequestError as exc:
            raise RequestError(401, "This device is no longer paired.") from exc

        if action == "join":
            return self.join(account_key, account_etag, record, device, secret, data)
        self.authenticate(record, device, secret)
        if action == "revoke":
            self.storage.put(account_key, {"devices": {device: digest(secret)}, "pair": None}, account_etag)
            return {}

        sidecar = checked(ID, data.get("sidecar"))
        prefix = f"sync/{account}/sidecars/{sidecar}"
        manifest_key = prefix + "/manifest.json"
        if action == "create":
            try:
                self.storage.put(manifest_key, {"files": {}})
            except RequestError as exc:
                if exc.status != 409:
                    raise
                self.storage.get(manifest_key)
            return {}

        stored, etag = self.storage.get(manifest_key)
        if action == "manifest":
            return {"files": stored["files"], "etag": etag}
        if action == "pair":
            code = secrets.token_urlsafe(32)
            record["pair"] = {"hash": digest(code), "sidecar": sidecar, "expires": int(time.time()) + 600}
            self.storage.put(account_key, record, account_etag)
            return {"code": f"{account}.{code}"}
        if action == "transfer":
            return self.transfer(prefix, data)
        if action == "commit":
            if data.get("etag") != etag:
                raise RequestError(409, "The cloud version changed. Sync again.")
            files = manifest(data.get("files"))
            prior = {item["digest"]: item["size"] for item in stored["files"].values()}
            needed = {item["digest"]: item["size"] for item in files.values()}
            additions = [(sha, size) for sha, size in needed.items() if prior.get(sha) != size]
            if len(additions) > 16:
                raise ValueError("Commit up to 16 new file versions at a time.")
            for sha, size in additions:
                self.storage.verify(f"{prefix}/blobs/{sha}", size)
            return {"etag": self.storage.put(manifest_key, {"files": files}, etag)}
        raise RequestError(404, "Unknown sync operation.")

    @staticmethod
    def authenticate(record: dict, device: str, secret: str) -> None:
        if not hmac.compare_digest(record["devices"].get(device, ""), digest(secret)):
            raise RequestError(401, "This device is no longer paired.")

    def join(self, key: str, etag: str, record: dict, device: str, secret: str, data: dict) -> dict:
        code = checked(SECRET, data.get("code"))
        pair = record.get("pair")
        if not pair or pair["expires"] < time.time() or not hmac.compare_digest(pair["hash"], digest(code)):
            raise RequestError(403, "The pairing code expired or is invalid.")
        devices = record["devices"]
        if pair.get("used_by") not in (None, device):
            raise RequestError(403, "This pairing code has already been used.")
        if device in devices:
            self.authenticate(record, device, secret)
        elif len(devices) >= 2:
            raise RequestError(409, "Revoke the other device before pairing a replacement.")
        devices[device] = digest(secret)
        pair["used_by"] = device
        self.storage.put(key, record, etag)
        return {"sidecar": pair["sidecar"]}

    def transfer(self, prefix: str, data: dict) -> dict:
        requested = data.get("files")
        mode = data.get("mode")
        if not isinstance(requested, list) or not 1 <= len(requested) <= 64:
            raise ValueError("Send up to 64 file versions at a time.")
        urls = {}
        for item in requested:
            version = blob(item)
            sha, size = version["digest"], version["size"]
            key = f"{prefix}/blobs/{sha}"
            if mode == "download":
                urls[sha] = self.storage.url("get_object", key)
            elif mode == "upload":
                urls[sha] = self.storage.url(
                    "put_object", key, IfNoneMatch="*", ContentLength=size, ContentType="application/octet-stream", ChecksumSHA256=checksum(sha),
                )
            else:
                raise ValueError("Invalid transfer mode.")
        return {"urls": urls}
