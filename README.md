# Knowledge Sidecar

Local-first knowledge sidecar proof of concept.

## Development

Create the local virtual environment and install development dependencies:

```bash
uv sync --group dev
```

Run the CLI:

```bash
uv run ksidecar --help
uv run ksidecar paths
```

Create and search a sidecar against any local directory:

```bash
uv run ksidecar sidecar create /path/to/project --id project
uv run ksidecar rebuild project
uv run ksidecar search project "needle" --mode keyword
uv run ksidecar status project
```

Run the API:

```bash
uv run uvicorn ksidecar.api:app --reload
```

Run the dashboard in another shell:

```bash
cd frontend
npm install
npm start
```

The dashboard runs at `http://localhost:4200` and proxies API calls to
`http://localhost:8000`.

Run tests:

```bash
uv run pytest
```

Run linting:

```bash
uv run ruff check .
```

Run static type checking with the same engine Pylance uses:

```bash
uv run pyright
```

Semantic search is local-only and optional for development. Install the embedding/vector
runtime before expecting rebuilds to write `vectors.lance/` tables:

```bash
uv sync --extra semantic --group dev
```

The semantic runtime uses `sentence-transformers` with `BAAI/bge-small-en-v1.5` and
stores LanceDB tables under each sidecar storage directory.

## Configuration

Resolved configuration can be inspected with:

```bash
uv run ksidecar config
```

Environment variables:

- `KSIDECAR_HOME`: app-managed storage path. Defaults to `~/.ksidecar`.
- `KSIDECAR_MAX_FILE_SIZE_BYTES`: maximum file size read by new sidecars.
- `KSIDECAR_IGNORED_DIRS`: comma-separated directory names skipped while scanning.
- `KSIDECAR_EMBEDDING_MODEL`: local `sentence-transformers` model name.

Per-sidecar overrides are available at creation time:

```bash
uv run ksidecar sidecar create /path/to/project \
  --id project \
  --max-file-size 1000000 \
  --ignore-dir node_modules \
  --ignore-dir .git \
  --embedding-model BAAI/bge-small-en-v1.5
```

## Smoke and Performance Check

Run the generated local fixture workflow before using a fresh clone on real data:

```bash
uv run ksidecar smoke --file-count 200 --max-search-seconds 1
```

The smoke command creates a temporary 100-2000 file style corpus, registers it as a
sidecar, rebuilds the keyword index, runs a search, reports rebuild/search timings, and
removes the generated sidecar metadata. It does not require cloud services.

## Frontend

The Angular dashboard can create sidecars, trigger rebuilds and refreshes, inspect files
and errors, run searches, manage watching, and copy MCP config.
