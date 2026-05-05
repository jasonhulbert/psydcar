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

## Frontend

The Angular dashboard is planned for a later phase. Phase 0 reserves `frontend/` but does
not create the Angular workspace yet, avoiding generated framework files before the API
surface is available.
