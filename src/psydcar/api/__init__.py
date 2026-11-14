"""Loopback HTTP adapter for the Psydcar app."""

from __future__ import annotations

import hmac
import re
import secrets
import socket
import sys
import threading
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..core.artifacts import Artifact
from ..core.proposals import Proposals
from ..core.sync import Sync
from ..core.workspace import Workspace
from ..evidence import MAX_FILE_BYTES
from ..files import MAX_PATH_LENGTH, FileConflict
from ..store import SidecarStore, StoreError
from ..watcher import SidecarWatcher, WatchEvent
from .preview import Previews


_SHARED_ASSETS = {
    "brand.svg", "design-system.css", "editor.css", "editor.js", "editor-licenses.txt", "icons.svg", "Geist-OFL.txt",
    "Geist-Variable.woff2", "GeistMono-Variable.woff2",
}
_MAX_BODY_BYTES = MAX_FILE_BYTES * 6 + 8192
_ASSET = re.compile(r"^[A-Za-z0-9_-]+\.(?:js|css|txt)$")
RelativePath = Annotated[str, Query(min_length=1, max_length=MAX_PATH_LENGTH)]
EntryPath = Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH)]
EntryType = Literal["file", "folder"]


class Session(BaseModel):
    token: str


class SidecarSummary(BaseModel):
    id: str
    name: str
    source_path: str
    status: str


class EntryChange(BaseModel):
    path: EntryPath
    type: EntryType


class FileEntry(BaseModel):
    path: str
    type: EntryType
    editable: bool = False
    image: bool = False
    viewable: bool = False
    tags: list[str] = Field(default_factory=list)
    size: int | None = None
    mtime_ns: int | None = None


class FileTree(BaseModel):
    entries: list[FileEntry]
    truncated: bool


class MoveEntry(BaseModel):
    source: EntryPath
    target: EntryPath


class EntryLocation(BaseModel):
    path: EntryPath


class Preview(BaseModel):
    path: str


class FileVersion(BaseModel):
    path: str
    version: str
    size: int
    mtime_ns: int
    modified_at: str


class TextFile(FileVersion):
    content: str
    newline: Literal["\n", "\r\n"]
    bom: bool


class SaveText(BaseModel):
    content: str = Field(max_length=MAX_FILE_BYTES)
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    newline: Literal["\n", "\r\n"] = "\n"
    bom: bool = False


class Conflict(BaseModel):
    detail: str
    version: str | None


class ProposalSummary(BaseModel):
    id: str
    path: str
    agent_name: str
    summary: str
    status: Literal["pending", "approved", "rejected"]
    created_at: str
    decided_at: str | None


class ProposalList(BaseModel):
    items: list[ProposalSummary]
    pending_count: int


class ProposalDetail(ProposalSummary):
    stale: bool
    diff: str | None
    before: str | None
    proposed: str | None


class ProposalReview(BaseModel):
    decision: Literal["approved", "rejected"]


class SyncConflictSummary(BaseModel):
    id: str
    path: str


class SyncStatus(BaseModel):
    available: bool
    account_active: bool
    linked: bool
    last_synced_at: str | None
    error: str | None
    revision: int
    conflicts: list[SyncConflictSummary]


class SyncVersion(BaseModel):
    digest: str
    size: int
    text: str | None


class SyncConflictDetail(SyncConflictSummary):
    diff: str | None
    local: SyncVersion | None
    cloud: SyncVersion | None


class SyncSetup(BaseModel):
    enrollment_key: str = Field(default="", max_length=256)


class SyncPairing(BaseModel):
    code: str = Field(max_length=128)


class SyncResolution(BaseModel):
    decision: Literal["local", "cloud", "merge"]
    local_digest: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    merged: str | None = Field(default=None, max_length=MAX_FILE_BYTES)


def create_app(store: SidecarStore, port: int, *, watch: bool = True, sync_service: Sync | None = None) -> FastAPI:
    host = f"127.0.0.1:{port}"
    origin = f"http://{host}"
    token = secrets.token_urlsafe(32)
    assets = Path(str(files("psydcar").joinpath("app_assets")))
    workspace = Workspace(store)
    proposals = Proposals(store)
    sync = sync_service if sync_service is not None else Sync(store)
    previews = Previews(port, origin)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if not watch:
            yield
            return
        stop = threading.Event()
        watcher = SidecarWatcher(store)

        def report(event: WatchEvent) -> None:
            if event.error:
                print(f"Psydcar watcher: {event.error}", file=sys.stderr)

        thread = threading.Thread(target=watcher.run, args=(stop, report), daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    app = FastAPI(title="Psydcar local API", version="1", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next) -> Response:
        request_origin = request.headers.get("origin")
        request_host = request.headers.get("host")
        if request_host == previews.host:
            return await run_in_threadpool(previews.serve, request)
        if request_host != host:
            return JSONResponse({"detail": "Use the local Psydcar app address."}, status_code=400)
        if request_origin is not None and request_origin != origin:
            return JSONResponse({"detail": "Open this request from the local Psydcar app."}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            supplied = request.headers.get("x-psydcar-token", "").encode()
            if request_origin != origin or not hmac.compare_digest(token.encode(), supplied):
                return JSONResponse({"detail": "The app session is invalid. Reload the page."}, status_code=403)
            length = request.headers.get("content-length", "")
            if not length.isascii() or not length.isdecimal() or request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "Send the request with a content length."}, status_code=411)
            if len(length) > 10 or int(length) > _MAX_BODY_BYTES:
                return JSONResponse({"detail": "The request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StoreError)
    async def store_error(_request: Request, exc: StoreError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileConflict)
    async def conflict(_request: Request, exc: FileConflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "version": exc.version}, status_code=409)

    @app.get("/api/v1/session", operation_id="session")
    def session() -> Session:
        return Session(token=token)

    @app.get("/api/v1/sidecars", operation_id="listSidecars")
    def list_sidecars() -> list[SidecarSummary]:
        return [
            SidecarSummary(id=item.id, name=item.name, source_path=str(item.source_path), status=item.status)
            for item in store.list()
        ]

    @app.get("/api/v1/sidecars/{sidecar_id}/files", operation_id="listFiles")
    def list_files(sidecar_id: str) -> FileTree:
        return FileTree.model_validate(workspace.files(sidecar_id).tree())

    @app.get("/api/v1/sidecars/{sidecar_id}/proposals", operation_id="listProposals")
    def list_proposals(sidecar_id: str, reviewed: bool = False) -> ProposalList:
        return ProposalList.model_validate(proposals.list(sidecar_id, reviewed=reviewed))

    @app.get("/api/v1/sidecars/{sidecar_id}/proposals/{proposal_id}", operation_id="openProposal")
    def open_proposal(sidecar_id: str, proposal_id: str) -> ProposalDetail:
        return ProposalDetail.model_validate(proposals.detail(sidecar_id, proposal_id))

    @app.post("/api/v1/sidecars/{sidecar_id}/proposals/{proposal_id}", operation_id="reviewProposal",
              responses={409: {"model": Conflict}})
    def review_proposal(sidecar_id: str, proposal_id: str, review: ProposalReview) -> ProposalSummary:
        return ProposalSummary.model_validate(proposals.review(sidecar_id, proposal_id, review.decision))

    @app.get("/api/v1/sidecars/{sidecar_id}/sync", operation_id="syncStatus")
    def sync_status(sidecar_id: str) -> SyncStatus:
        return SyncStatus.model_validate(sync.status(sidecar_id))

    @app.post("/api/v1/sidecars/{sidecar_id}/sync", operation_id="syncNow")
    def sync_now(sidecar_id: str) -> SyncStatus:
        return SyncStatus.model_validate(sync.run(sidecar_id))

    @app.post("/api/v1/sidecars/{sidecar_id}/sync/enable", operation_id="enableSync")
    def enable_sync(sidecar_id: str, setup: SyncSetup) -> SyncStatus:
        return SyncStatus.model_validate(sync.enable(sidecar_id, setup.enrollment_key))

    @app.post("/api/v1/sidecars/{sidecar_id}/sync/join", operation_id="joinSync")
    def join_sync(sidecar_id: str, pairing: SyncPairing) -> SyncStatus:
        return SyncStatus.model_validate(sync.join(sidecar_id, pairing.code))

    @app.post("/api/v1/sidecars/{sidecar_id}/sync/pair", operation_id="pairSync")
    def pair_sync(sidecar_id: str) -> SyncPairing:
        return SyncPairing(code=sync.pair(sidecar_id))

    @app.post("/api/v1/sidecars/{sidecar_id}/sync/revoke", operation_id="revokeSync")
    def revoke_sync(sidecar_id: str) -> SyncStatus:
        sync.revoke(sidecar_id)
        return SyncStatus.model_validate(sync.status(sidecar_id))

    @app.get("/api/v1/sidecars/{sidecar_id}/sync/conflicts/{conflict_id}", operation_id="syncConflict")
    def sync_conflict(sidecar_id: str, conflict_id: str) -> SyncConflictDetail:
        return SyncConflictDetail.model_validate(sync.conflict(sidecar_id, conflict_id))

    @app.get("/api/v1/sidecars/{sidecar_id}/sync/conflicts/{conflict_id}/download", operation_id="downloadSyncConflict",
             response_class=Response)
    def download_sync_conflict(sidecar_id: str, conflict_id: str, copy: Literal["local", "cloud"],
                               digest: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")]) -> Response:
        name, data = sync.conflict_bytes(sidecar_id, conflict_id, copy, digest)
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"})

    @app.post("/api/v1/sidecars/{sidecar_id}/sync/conflicts/{conflict_id}", operation_id="resolveSyncConflict",
              responses={409: {"model": Conflict}})
    def resolve_sync_conflict(sidecar_id: str, conflict_id: str, resolution: SyncResolution) -> SyncStatus:
        return SyncStatus.model_validate(sync.resolve(sidecar_id, conflict_id, resolution.local_digest,
                                                      resolution.decision, resolution.merged))

    @app.post("/api/v1/sidecars/{sidecar_id}/files", operation_id="createEntry", status_code=201)
    def create_entry(sidecar_id: str, entry: EntryChange) -> EntryChange:
        with workspace.edit(sidecar_id) as source:
            return EntryChange.model_validate(source.create(entry.path, entry.type))

    @app.post("/api/v1/sidecars/{sidecar_id}/files/move", operation_id="moveEntry")
    def move_entry(sidecar_id: str, entry: MoveEntry) -> EntryChange:
        with workspace.edit(sidecar_id) as source:
            return EntryChange.model_validate(source.move(entry.source, entry.target))

    @app.delete("/api/v1/sidecars/{sidecar_id}/files", operation_id="deleteEntry")
    def delete_entry(sidecar_id: str, entry: EntryLocation) -> EntryChange:
        with workspace.edit(sidecar_id) as source:
            return EntryChange.model_validate(source.delete(entry.path))

    @app.post("/api/v1/sidecars/{sidecar_id}/previews", operation_id="startPreview", status_code=201)
    def start_preview(sidecar_id: str, entry: EntryLocation) -> Preview:
        artifact = Artifact.from_entry(workspace.files(sidecar_id).root, entry.path)
        return Preview(path=previews.start(artifact))

    @app.get("/api/v1/sidecars/{sidecar_id}/file", operation_id="openFile")
    def open_file(sidecar_id: str, path: RelativePath) -> TextFile:
        return TextFile.model_validate(workspace.files(sidecar_id).read(path))

    @app.put("/api/v1/sidecars/{sidecar_id}/file", operation_id="saveFile", responses={409: {"model": Conflict}})
    def save_file(sidecar_id: str, path: RelativePath, change: SaveText) -> FileVersion:
        with workspace.edit(sidecar_id) as source:
            saved = source.write(path, change.content, change.version, change.newline, change.bom)
        return FileVersion.model_validate(saved)

    @app.get("/api/v1/sidecars/{sidecar_id}/image", operation_id="viewImage", response_class=FileResponse)
    def view_image(sidecar_id: str, path: RelativePath) -> FileResponse:
        source, media_type, etag = workspace.files(sidecar_id).image(path)
        return FileResponse(source, media_type=media_type, headers={
            "ETag": etag, "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        })

    @app.get("/assets/{name}", include_in_schema=False)
    def shared_asset(name: str) -> FileResponse:
        if name not in _SHARED_ASSETS:
            raise HTTPException(404)
        return FileResponse(str(files("psydcar").joinpath("dashboard_assets", name)))

    @app.get("/", include_in_schema=False)
    @app.get("/sidecars/{sidecar_id}", include_in_schema=False)
    def shell(sidecar_id: str = "") -> HTMLResponse:
        index = assets / "index.html"
        if not index.is_file():
            raise HTTPException(503, "App assets are missing. Build the app with npm --prefix app run build.")
        nonce = secrets.token_urlsafe(18)
        markup = index.read_text(encoding="utf-8").replace("PSYDCAR_STYLE_NONCE", nonce)
        return HTMLResponse(markup, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                f"default-src 'self'; script-src 'self'; style-src 'self' 'nonce-{nonce}'; "
                "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; "
                f"base-uri 'self'; form-action 'none'; frame-src {previews.origin}"
            ),
        })

    @app.get("/{name}", include_in_schema=False)
    def bundle(name: str) -> FileResponse:
        target = assets / name
        if not _ASSET.fullmatch(name) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    return app


def run_app(store: SidecarStore, port: int = 8765, open_browser: bool = True) -> None:
    if not 0 <= port <= 65535:
        raise StoreError("App port must be between 0 and 65535.")
    if not files("psydcar").joinpath("app_assets", "index.html").is_file():
        raise StoreError("App assets are missing. Build them with npm --prefix app run build.")

    class LocalServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(f"Psydcar app is running at {url}", flush=True)
                if open_browser:
                    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise StoreError(f"Cannot start the local app: {exc}") from exc
        port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(create_app(store, port), log_level="warning", proxy_headers=False)
        LocalServer(config).run(sockets=[listener])
