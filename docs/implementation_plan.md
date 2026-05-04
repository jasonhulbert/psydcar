# Knowledge Sidecar POC - Phase Implementation Plan

This plan breaks the spec into implementation phases that can be executed incrementally by GPT. Each phase should leave the repository in a runnable or verifiable state and should avoid depending on manual setup beyond ordinary local commands.

## Guiding Principles

- Build the backend core first, because indexing, storage, and search are the system contract used by the MCP server, API, and dashboard.
- Keep the first version intentionally local and simple: sidecar source directories may live anywhere on the local filesystem, while app-managed metadata and indexes live under `~/.ksidecar`.
- Prefer small public interfaces between modules so later phases can add MCP, FastAPI, and Angular without rewriting the indexing core.
- Treat security constraints as baseline behavior from the first file-access phase: root confinement, path traversal checks, file size limits, and binary-file skipping.
- Add focused tests around indexing, path safety, refresh behavior, and search merging as those pieces appear.

## Phase 0 - Repository Scaffold and Developer Workflow

Goal: create a minimal, repeatable project structure for backend, frontend, CLI, tests, and local execution.

Deliverables:

- Python package scaffold for the backend and CLI.
- Dependency management files for the Python app.
- Basic CLI entrypoint named `ksidecar`.
- Initial test runner setup.
- Project configuration for formatting and linting.
- Placeholder Angular workspace decision documented, but the Angular app does not need to exist yet.

Suggested structure:

```text
ksidecar/
  backend/
    ksidecar/
      __init__.py
      cli.py
      config.py
      paths.py
    tests/
  frontend/
  docs/
```

Acceptance checks:

- `ksidecar --help` runs locally.
- Python tests can run even if they only cover config/path helpers.
- The app storage root can be resolved, defaulting to `~/.ksidecar`.

## Phase 1 - Sidecar Registry and Filesystem Safety

Goal: implement sidecar creation, discovery, metadata persistence, and safe path handling before indexing.

Deliverables:

- Sidecar registry under `~/.ksidecar/sidecars/<id>/` for app-managed metadata and indexes.
- Sidecar source root selection from any local directory path supplied by the user.
- `sidecar.json` model with id, name, root path, created/updated timestamps, indexing status, and config.
- Sidecar create/list/delete operations in core Python services.
- Safe root-relative path resolution against the sidecar source root, not against `~/.ksidecar`.
- File filtering utilities:
  - allowed text/code extensions
  - max file size enforcement
  - binary detection
  - ignored directories such as `.git`, `node_modules`, virtualenvs, build outputs

Acceptance checks:

- Creating a sidecar writes the expected storage layout.
- Creating a sidecar can target a fixture directory outside `~/.ksidecar`.
- Listing sidecars reads persisted metadata.
- Attempts to resolve paths outside the sidecar root are rejected.
- Binary and oversized files are skipped by scanner tests.

## Phase 2 - SQLite Document and Chunk Index

Goal: implement the durable keyword-search index with document and chunk persistence.

Deliverables:

- SQLite schema for documents, chunks, FTS table, and indexing errors.
- Schema migration/init helper.
- File scanner that produces document candidates from the configured sidecar source root.
- Content reader with encoding fallback and skip behavior.
- Chunkers:
  - Markdown split by headings
  - code/text line chunks around 140 lines with 20-line overlap
  - JSON/YAML line-based chunks
- Full rebuild flow that scans, chunks, and writes documents/chunks/FTS rows.
- Keyword search over SQLite FTS.

Acceptance checks:

- Rebuild indexes a small fixture directory.
- Document and chunk rows match expected fixture files.
- Keyword search returns chunk id, relative path, line range, snippet/text preview, and score.
- Deleted/rebuilt indexes do not leave stale rows.

## Phase 3 - Incremental Refresh

Goal: update only changed files while keeping SQLite and FTS consistent.

Deliverables:

- Content hash and modified-time based change detection.
- Refresh operation for new, modified, unchanged, and deleted files.
- Per-file error capture without failing the whole refresh.
- Sidecar status fields for last refresh, indexed file count, chunk count, and error count.

Acceptance checks:

- Modifying one file replaces only that file's chunks.
- Deleting a file removes document, chunk, and FTS entries.
- Unchanged files are not re-chunked.
- Errors are visible through the core service API.

## Phase 4 - Embeddings and LanceDB Vector Search

Goal: add semantic search while preserving the already-working FTS index.

Deliverables:

- Local embedding service using `sentence-transformers` with `BAAI/bge-small-en-v1.5`.
- LanceDB table stored under `vectors.lance/`.
- Vector rows linked to chunk ids and content hashes.
- Rebuild and refresh integration for vector inserts/deletes.
- Semantic search API returning the same result shape as keyword search.
- Graceful first-run model loading behavior and clear local-only assumptions.

Acceptance checks:

- Rebuild writes vectors for indexed chunks.
- Semantic search returns relevant fixture chunks.
- Modified/deleted chunks update LanceDB consistently.
- If the embedding model is unavailable, the error is surfaced clearly rather than corrupting the index.

## Phase 5 - Hybrid Search

Goal: provide the default search mode by combining keyword and semantic results.

Deliverables:

- Search service supporting `keyword`, `semantic`, and `hybrid`.
- Score normalization for FTS and vector results.
- Merge and deduplication by chunk id.
- Configurable result limit.
- Stable result payload shared by CLI, API, and MCP.

Acceptance checks:

- Hybrid search includes both exact keyword matches and semantic matches.
- Duplicate chunk ids are merged into a single result.
- Search completes within the POC target for fixture datasets.
- Tests cover ranking and deduplication behavior.

## Phase 6 - CLI Surface

Goal: expose core sidecar operations without requiring the dashboard or MCP.

Deliverables:

- CLI commands:
- `ksidecar sidecar create`
  - `ksidecar sidecar list`
  - `ksidecar sidecar delete`
  - `ksidecar rebuild`
  - `ksidecar refresh`
  - `ksidecar search`
  - `ksidecar status`
- Human-readable output by default.
- JSON output option for commands that return structured data.

Acceptance checks:

- A sidecar can be created from an arbitrary local directory, rebuilt, searched, refreshed, and inspected entirely from the CLI.
- CLI commands call the same services that later API and MCP layers will use.

## Phase 7 - MCP Server

Goal: allow agent clients to query one or more sidecars through MCP over stdio.

Deliverables:

- `ksidecar mcp --sidecars frontend,backend` command.
- MCP tools:
  - `search`
  - `read_chunk`
  - `read_file`
  - `list_files`
  - `get_status`
- Multi-sidecar query routing and result labeling.
- Read-only behavior for all MCP tools.
- Path traversal protection reused from the core filesystem module.

Acceptance checks:

- MCP server starts over stdio.
- Each tool works against a test sidecar.
- `read_file` cannot access files outside the sidecar root.
- Multi-sidecar search returns sidecar identifiers with results.

## Phase 8 - FastAPI Backend

Goal: expose the sidecar services through the REST API needed by the dashboard.

Deliverables:

- FastAPI app and startup configuration.
- API endpoints from the spec:
  - `GET /api/sidecars`
  - `POST /api/sidecars`
  - `DELETE /api/sidecars/{id}`
  - `POST /api/sidecars/{id}/refresh`
  - `POST /api/sidecars/{id}/rebuild`
  - `GET /api/sidecars/{id}/files`
  - `GET /api/sidecars/{id}/errors`
  - `GET /api/sidecars/{id}/mcp-config`
- Add search preview endpoint, since the dashboard requires Search preview:
  - `GET /api/sidecars/{id}/search`
- Request/response models with clear validation errors.
- Background task handling for refresh/rebuild so long indexing jobs do not block API responses.

Acceptance checks:

- API can create/list/delete sidecars whose source roots are arbitrary local directories.
- API can trigger rebuild and refresh.
- API exposes files, errors, status, MCP config, and search results.
- Endpoint tests cover success and validation failures.

## Phase 9 - Angular Dashboard

Goal: create the local dashboard UI for managing and inspecting sidecars.

Deliverables:

- Angular 21 standalone app in `frontend/`.
- API client service.
- Views:
  - sidecar list
  - create sidecar
  - status view
  - file list
  - error list
  - MCP config copy
  - search preview
- Practical local dev wiring to the FastAPI backend.
- Empty, loading, error, and indexing states.

Acceptance checks:

- Dashboard can create a sidecar and display it.
- Dashboard create flow lets the user enter or select any local directory path available to the backend process.
- Dashboard can trigger rebuild/refresh.
- Dashboard can search a sidecar.
- Dashboard can show MCP config and copy it.
- UI works at desktop and narrow widths without text overlap.

## Phase 10 - File Watching

Goal: add automatic local refresh with debounce and batch updates.

Deliverables:

- Watchdog-based watcher service.
- Debounce window of 1-2 seconds.
- Batch refresh of changed paths.
- CLI command or backend lifecycle option to start watching.
- Watch status exposed through core services and API.

Acceptance checks:

- Creating, editing, and deleting files updates the index after debounce.
- Burst file changes are batched into one refresh operation.
- Watcher failures are captured as sidecar errors.

## Phase 11 - POC Hardening and Packaging

Goal: make the POC reliable enough to run repeatedly on real local directories.

Deliverables:

- End-to-end smoke workflow documented in the README.
- Configuration reference for storage path, max file size, ignored dirs, and embedding model.
- Better error messages for missing dependencies, unavailable embedding model, and corrupt sidecar storage.
- Basic performance checks against generated fixture datasets near the target range.
- Packaging metadata for local install.

Acceptance checks:

- Fresh clone can install and run the CLI, API, and dashboard with documented commands.
- A 100-2000 file test corpus can be indexed and searched.
- Search target remains under roughly 1 second for normal POC-sized datasets.
- No cloud services are required.

## Cross-Phase Technical Decisions

These decisions should be made early and kept stable unless implementation proves them wrong:

- Python CLI framework: use Typer unless a lighter standard-library CLI is preferred after scaffold.
- Python package manager: use the project-standard tool selected in Phase 0 and keep commands documented.
- Sidecar ids: use stable slug ids with collision handling, not raw directory names.
- Sidecar source roots: store absolute resolved paths and do not copy source files into app storage.
- SQLite schema ownership: one module owns schema creation and migrations.
- Result payload: define once in the core search service, then reuse across CLI, API, and MCP.
- File read limits: enforce maximum file size before reading content.
- Refresh locking: prevent concurrent rebuild/refresh of the same sidecar.

## Recommended Execution Order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 6
6. Phase 4
7. Phase 5
8. Phase 7
9. Phase 8
10. Phase 9
11. Phase 10
12. Phase 11

This order brings up a usable CLI-backed system before adding embeddings, MCP, API, dashboard, and watching. It also creates a fallback path: keyword indexing remains useful even if local embedding setup takes extra iteration.
