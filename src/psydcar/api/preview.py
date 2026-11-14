"""Serve short-lived artifact previews on a host with no app routes."""

from __future__ import annotations

import secrets
import threading
import time
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response

from ..core.artifacts import Artifact
from ..store import StoreError


_LIFETIME = 30 * 60
_MAX_PREVIEWS = 64


class Previews:
    def __init__(self, port: int, app_origin: str):
        self.host = f"localhost:{port}"
        self.origin = f"http://{self.host}"
        self.app_origin = app_origin
        self._entries: dict[str, tuple[float, Artifact]] = {}
        self._lock = threading.Lock()

    def start(self, artifact: Artifact) -> str:
        key = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            while self._entries:
                oldest = next(iter(self._entries))
                if len(self._entries) < _MAX_PREVIEWS and self._entries[oldest][0] > now:
                    break
                del self._entries[oldest]
            self._entries[key] = (now + _LIFETIME, artifact)
        return f"/{key}/{quote(artifact.entry, safe='')}"

    def serve(self, request: Request) -> Response:
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405, headers={"Allow": "GET, HEAD"})
        origin = request.headers.get("origin")
        if origin not in {None, "null"}:
            return Response(status_code=403)
        key, separator, path = request.scope["path"][1:].partition("/")
        with self._lock:
            item = self._entries.get(key) if separator else None
        if item is None or item[0] <= time.monotonic():
            return Response("Preview unavailable. Reopen it in Psydcar.", status_code=404, media_type="text/plain")
        artifact = item[1]
        if path.endswith("/"):
            path += "index.html"
        try:
            data, media_type = artifact.read(path or artifact.entry)
        except (OSError, StoreError):
            return Response(status_code=404)

        scope = f"{self.origin}/{key}/"
        headers = {
            "Cache-Control": "no-store",
            "Content-Length": str(len(data)),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src {scope} 'unsafe-inline'; style-src {scope} 'unsafe-inline'; "
                f"img-src {scope} data: blob:; font-src {scope} data:; media-src {scope} data: blob:; "
                f"connect-src {scope}; base-uri {scope}; form-action 'none'; "
                f"frame-ancestors {self.app_origin}; sandbox allow-scripts"
            ),
        }
        if origin == "null":
            headers["Access-Control-Allow-Origin"] = "null"
            headers["Vary"] = "Origin"
        return Response(data if request.method == "GET" else b"", media_type=media_type, headers=headers)
