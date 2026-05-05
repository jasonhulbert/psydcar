# KSidecar Dashboard

Angular 21 standalone dashboard for the local FastAPI backend.

## Local development

Install dependencies:

```bash
npm install
```

Start the backend from the repository root:

```bash
uv run uvicorn ksidecar.api:app --app-dir backend --reload
```

Start the dashboard from this directory:

```bash
npm start
```

The Angular dev server runs at `http://localhost:4200/` and proxies `/api` requests to
`http://127.0.0.1:8000`.

## Checks

```bash
npm run build
npm test
```
