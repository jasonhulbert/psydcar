"""Desktop client for the hosted control endpoint and signed transfers."""

from __future__ import annotations

import json
from http.client import HTTPException
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..store import StoreError
from ..sync_protocol import MAX_MANIFEST, Blob, checksum, encode, manifest


class RemoteError(StoreError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _https(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname and not (parsed.username or parsed.password or parsed.fragment):
            return url
    except ValueError:
        pass
    raise StoreError("Sync requires an HTTPS service address.")


class SyncRemote:
    def __init__(self, endpoint: str, credential: str):
        self.endpoint = _https(endpoint)
        self.credential = credential
        self.opener = build_opener(NoRedirect())

    def _request(self, url: str, method: str, cap: int, data: bytes | None = None,
                 headers: dict[str, str] | None = None, *, allow_existing: bool = False) -> bytes:
        try:
            # Stream uploads in chunks so a slow, active connection can continue.
            body = BytesIO(data) if method == "PUT" and data is not None else data
            request = Request(_https(url), data=body, method=method, headers=headers or {})
            with self.opener.open(request, timeout=20) as response:
                output = response.read(cap + 1)
        except HTTPError as exc:
            with exc:
                if allow_existing and exc.code == 412:
                    return b""
                detail = "The sync service could not complete the request."
                if url == self.endpoint:
                    try:
                        body = json.loads(exc.read(4096))
                        if isinstance(body, dict) and isinstance(body.get("detail"), str):
                            detail = body["detail"]
                    except (ValueError, OSError):
                        pass
                raise RemoteError(detail, exc.code) from exc
        except (URLError, OSError, HTTPException) as exc:
            raise RemoteError("The cloud is unreachable. Local changes are saved and sync can retry.") from exc
        if len(output) > cap:
            raise RemoteError("The sync service sent more data than expected.")
        return output

    def call(self, action: str, **payload) -> dict:
        raw = self._request(self.endpoint, "POST", MAX_MANIFEST + 4096, encode({"action": action, **payload}),
                            {"Authorization": f"Bearer {self.credential}", "Content-Type": "application/json"})
        try:
            result = json.loads(raw)
            if isinstance(result, dict):
                return result
        except ValueError:
            pass
        raise RemoteError("The sync service returned an invalid response.")

    def snapshot(self, sidecar: str) -> tuple[dict[str, Blob], str]:
        result = self.call("manifest", sidecar=sidecar)
        try:
            files = manifest(result.get("files"))
        except ValueError as exc:
            raise RemoteError(str(exc)) from exc
        etag = result.get("etag")
        if not isinstance(etag, str) or not etag:
            raise RemoteError("The sync manifest has no version.")
        return files, etag

    def urls(self, sidecar: str, mode: str, files: list[Blob]) -> dict[str, str]:
        response = self.call("transfer", sidecar=sidecar, mode=mode, files=files).get("urls")
        if not isinstance(response, dict) or any(not isinstance(response.get(item["digest"]), str) for item in files):
            raise RemoteError("The sync service did not provide a file transfer URL.")
        return response

    def upload(self, url: str, data: bytes, blob: Blob) -> None:
        self._request(url, "PUT", 0, data, {
            "If-None-Match": "*", "Content-Length": str(blob["size"]), "Content-Type": "application/octet-stream",
            "x-amz-checksum-sha256": checksum(blob["digest"]),
        }, allow_existing=True)

    def download(self, url: str, blob: Blob) -> bytes:
        return self._request(url, "GET", blob["size"])
