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

## Frontend

The Angular dashboard is planned for a later phase. Phase 0 reserves `frontend/` but does
not create the Angular workspace yet, avoiding generated framework files before the API
surface is available.
