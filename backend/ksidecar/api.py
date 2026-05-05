"""FastAPI application exposing sidecar services for the dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ksidecar import __version__
from ksidecar.config import AppConfig
from ksidecar.index import (
    IndexingErrorRecord,
    SearchResult,
    list_sidecar_indexing_errors,
    rebuild_sidecar_index,
    refresh_sidecar_index,
)
from ksidecar.scanner import scan_files
from ksidecar.search import DEFAULT_SEARCH_LIMIT, SearchError, SearchMode, search_sidecar
from ksidecar.sidecars import (
    Sidecar,
    SidecarAlreadyExistsError,
    SidecarConfig,
    SidecarError,
    SidecarNotFoundError,
    SidecarRegistry,
)
from ksidecar.watcher import SidecarWatchService, WatcherError, WatchStatus


class SidecarConfigResponse(BaseModel):
    max_file_size_bytes: int


class SidecarResponse(BaseModel):
    id: str
    name: str
    root_path: str
    created_at: str
    updated_at: str
    indexing_status: str
    last_refresh_at: str | None
    indexed_file_count: int
    chunk_count: int
    error_count: int
    config: SidecarConfigResponse


class CreateSidecarRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_root: Path
    name: str | None = None
    sidecar_id: str | None = Field(default=None, alias="id")
    max_file_size_bytes: int = Field(default=1_000_000, gt=0)

    @field_validator("name", "sidecar_id")
    @classmethod
    def validate_optional_non_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be empty")
        return value


class OperationResponse(BaseModel):
    sidecar_id: str
    operation: Literal["refresh", "rebuild"]
    status: Literal["queued"]


class FileResponse(BaseModel):
    relative_path: str
    extension: str
    size_bytes: int


class FilesResponse(BaseModel):
    sidecar_id: str
    files: list[FileResponse]


class ErrorsResponse(BaseModel):
    sidecar_id: str
    errors: list[dict[str, str]]


class McpConfigResponse(BaseModel):
    sidecar_id: str
    command: str
    args: list[str]
    config: dict[str, Any]


class SearchResponse(BaseModel):
    sidecar_id: str
    query: str
    mode: SearchMode
    results: list[dict[str, Any]]


class WatchStatusResponse(BaseModel):
    sidecar_id: str
    active: bool
    debounce_seconds: float
    pending_path_count: int
    last_event_at: str | None
    last_refresh_at: str | None
    last_batch_size: int
    refresh_count: int
    last_error: str | None


class HealthResponse(BaseModel):
    name: str
    version: str


class DirectoryEntryResponse(BaseModel):
    name: str
    path: str


class DirectoryBrowseResponse(BaseModel):
    path: str
    parent_path: str | None
    entries: list[DirectoryEntryResponse]


router = APIRouter()


def create_app(config: AppConfig | None = None, *, start_watchers: bool = False) -> FastAPI:
    """Create the dashboard API application."""

    app_config = config or AppConfig.load()
    watch_service = SidecarWatchService(SidecarRegistry(app_config.storage_root))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_watchers:
            watch_service.start_all()
        try:
            yield
        finally:
            watch_service.stop_all()

    app = FastAPI(title="Knowledge Sidecar API", version=__version__, lifespan=lifespan)
    app.state.config = app_config
    app.state.watch_service = watch_service
    app.include_router(router)
    app.dependency_overrides[get_registry] = make_registry_dependency(app_config)

    @app.exception_handler(SidecarNotFoundError)
    async def sidecar_not_found_handler(_: Any, exc: SidecarNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SidecarAlreadyExistsError)
    async def sidecar_conflict_handler(_: Any, exc: SidecarAlreadyExistsError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SearchError)
    async def search_error_handler(_: Any, exc: SearchError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SidecarError)
    async def sidecar_error_handler(_: Any, exc: SidecarError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(WatcherError)
    async def watcher_error_handler(_: Any, exc: WatcherError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OSError)
    async def os_error_handler(_: Any, exc: OSError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    return app


def get_registry() -> SidecarRegistry:
    return SidecarRegistry(AppConfig.load().storage_root)


RegistryDependency = Annotated[SidecarRegistry, Depends(get_registry)]


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(name="ksidecar", version=__version__)


@router.get("/api/directories", response_model=DirectoryBrowseResponse)
def browse_directories(path: str | None = None) -> DirectoryBrowseResponse:
    current = Path(path).expanduser() if path else Path.home()
    current = current.resolve()
    if not current.is_dir():
        raise OSError(f"directory does not exist: {current}")

    entries: list[DirectoryEntryResponse] = []
    for child in sorted(current.iterdir(), key=lambda candidate: candidate.name.casefold()):
        try:
            if child.is_dir():
                entries.append(DirectoryEntryResponse(name=child.name, path=str(child.resolve())))
        except OSError:
            continue

    parent = current.parent if current.parent != current else None
    return DirectoryBrowseResponse(
        path=str(current),
        parent_path=str(parent) if parent else None,
        entries=entries,
    )


@router.get("/api/sidecars", response_model=list[SidecarResponse])
def list_sidecars(registry: RegistryDependency) -> list[SidecarResponse]:
    return [sidecar_to_response(sidecar) for sidecar in registry.list()]


@router.post(
    "/api/sidecars",
    response_model=SidecarResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sidecar(
    request: CreateSidecarRequest,
    registry: RegistryDependency,
) -> SidecarResponse:
    sidecar = registry.create(
        request.source_root,
        name=request.name,
        sidecar_id=request.sidecar_id,
        config=SidecarConfig(max_file_size_bytes=request.max_file_size_bytes),
    )
    return sidecar_to_response(sidecar)


@router.delete("/api/sidecars/{sidecar_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sidecar(sidecar_id: str, registry: RegistryDependency) -> None:
    registry.delete(sidecar_id)


@router.post("/api/sidecars/{sidecar_id}/refresh", response_model=OperationResponse)
def refresh_sidecar(
    sidecar_id: str,
    background_tasks: BackgroundTasks,
    registry: RegistryDependency,
) -> OperationResponse:
    registry.get(sidecar_id)
    background_tasks.add_task(run_index_task, registry.storage_root, sidecar_id, "refresh")
    return OperationResponse(sidecar_id=sidecar_id, operation="refresh", status="queued")


@router.post("/api/sidecars/{sidecar_id}/rebuild", response_model=OperationResponse)
def rebuild_sidecar(
    sidecar_id: str,
    background_tasks: BackgroundTasks,
    registry: RegistryDependency,
) -> OperationResponse:
    registry.get(sidecar_id)
    background_tasks.add_task(run_index_task, registry.storage_root, sidecar_id, "rebuild")
    return OperationResponse(sidecar_id=sidecar_id, operation="rebuild", status="queued")


@router.get("/api/sidecars/{sidecar_id}/watch", response_model=WatchStatusResponse)
def watch_status(
    sidecar_id: str,
    request: Request,
    registry: RegistryDependency,
) -> WatchStatusResponse:
    registry.get(sidecar_id)
    return watch_status_to_response(request.app.state.watch_service.status(sidecar_id))


@router.post("/api/sidecars/{sidecar_id}/watch", response_model=WatchStatusResponse)
def start_watch(
    sidecar_id: str,
    request: Request,
    registry: RegistryDependency,
) -> WatchStatusResponse:
    registry.get(sidecar_id)
    return watch_status_to_response(request.app.state.watch_service.start(sidecar_id))


@router.delete("/api/sidecars/{sidecar_id}/watch", response_model=WatchStatusResponse)
def stop_watch(
    sidecar_id: str,
    request: Request,
    registry: RegistryDependency,
) -> WatchStatusResponse:
    registry.get(sidecar_id)
    return watch_status_to_response(request.app.state.watch_service.stop(sidecar_id))


@router.get("/api/sidecars/{sidecar_id}/files", response_model=FilesResponse)
def list_files(sidecar_id: str, registry: RegistryDependency) -> FilesResponse:
    sidecar = registry.get(sidecar_id)
    files = [
        FileResponse(
            relative_path=candidate.relative_path.as_posix(),
            extension=candidate.extension,
            size_bytes=candidate.size_bytes,
        )
        for candidate in scan_files(
            sidecar.root_path,
            max_file_size_bytes=sidecar.config.max_file_size_bytes,
        )
    ]
    return FilesResponse(sidecar_id=sidecar.id, files=files)


@router.get("/api/sidecars/{sidecar_id}/errors", response_model=ErrorsResponse)
def list_errors(sidecar_id: str, registry: RegistryDependency) -> ErrorsResponse:
    registry.get(sidecar_id)
    errors = list_sidecar_indexing_errors(registry, sidecar_id)
    return ErrorsResponse(
        sidecar_id=sidecar_id,
        errors=[indexing_error_to_dict(error) for error in errors],
    )


@router.get("/api/sidecars/{sidecar_id}/mcp-config", response_model=McpConfigResponse)
def mcp_config(sidecar_id: str, registry: RegistryDependency) -> McpConfigResponse:
    sidecar = registry.get(sidecar_id)
    args = ["mcp", "--sidecars", sidecar.id]
    return McpConfigResponse(
        sidecar_id=sidecar.id,
        command="ksidecar",
        args=args,
        config={
            "mcpServers": {
                f"ksidecar-{sidecar.id}": {
                    "command": "ksidecar",
                    "args": args,
                }
            }
        },
    )


@router.get("/api/sidecars/{sidecar_id}/search", response_model=SearchResponse)
def search(
    sidecar_id: str,
    registry: RegistryDependency,
    q: Annotated[str, Query(min_length=1)],
    mode: SearchMode = "hybrid",
    limit: Annotated[int, Query(gt=0)] = DEFAULT_SEARCH_LIMIT,
) -> SearchResponse:
    results = search_sidecar(registry, sidecar_id, q, mode=mode, limit=limit)
    return SearchResponse(
        sidecar_id=sidecar_id,
        query=q,
        mode=mode,
        results=[search_result_to_dict(result) for result in results],
    )


def run_index_task(storage_root: Path | None, sidecar_id: str, operation: str) -> None:
    registry = SidecarRegistry(storage_root)
    if operation == "refresh":
        refresh_sidecar_index(registry, sidecar_id)
        return
    if operation == "rebuild":
        rebuild_sidecar_index(registry, sidecar_id)
        return
    raise ValueError(f"unsupported indexing operation: {operation}")


def sidecar_to_response(sidecar: Sidecar) -> SidecarResponse:
    payload = sidecar.to_json_dict()
    return SidecarResponse(**payload)


def indexing_error_to_dict(error: IndexingErrorRecord) -> dict[str, str]:
    return {key: str(value) for key, value in asdict(error).items()}


def search_result_to_dict(result: SearchResult) -> dict[str, Any]:
    return asdict(result)


def watch_status_to_response(status: WatchStatus) -> WatchStatusResponse:
    return WatchStatusResponse(
        sidecar_id=status.sidecar_id,
        active=status.active,
        debounce_seconds=status.debounce_seconds,
        pending_path_count=status.pending_path_count,
        last_event_at=status.last_event_at.isoformat() if status.last_event_at else None,
        last_refresh_at=status.last_refresh_at.isoformat() if status.last_refresh_at else None,
        last_batch_size=status.last_batch_size,
        refresh_count=status.refresh_count,
        last_error=status.last_error,
    )


def make_registry_dependency(config: AppConfig) -> Callable[[], SidecarRegistry]:
    def dependency() -> SidecarRegistry:
        return SidecarRegistry(config.storage_root)

    return dependency


app = create_app()
