"""Loopback-only browser management for the shared local sidecar store."""

from __future__ import annotations

import hmac
import importlib.util
import json
import re
import secrets
import signal
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from .embeddings import EmbeddingClient, EmbeddingError, ProviderConfig, ProviderStore
from .evidence import EvidenceStore
from .mcp_server import selected_ids
from .store import SidecarStore, StoreError
from .watcher import SidecarWatcher, WatchEvent


_SIDECAR_ACTION = re.compile(r"^/api/sidecars/([0-9a-f-]{36})/(rename|update)$")
_SIDECAR_ITEM = re.compile(r"^/api/sidecars/([0-9a-f-]{36})$")
_CONNECTION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/assets/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
}


def _provider_view(config: ProviderConfig | None) -> dict[str, Any]:
    if config is None:
        return {"configured": False, "state": "unconfigured"}
    available = config.credential_available
    return {
        "configured": True, "state": "ready" if available else "missing_credential",
        "base_url": config.base_url, "model": config.model,
        "api_key_env": config.api_key_env, "credential_available": available,
        "allow_insecure_http": config.allow_insecure_http,
        "configured_at": config.configured_at,
    }


def _provider_draft(provider: ProviderStore, payload: dict[str, Any]) -> ProviderConfig:
    base_url, model = payload.get("base_url"), payload.get("model")
    api_key_env = payload.get("api_key_env")
    if api_key_env == "":
        api_key_env = None
    allow_insecure_http = payload.get("allow_insecure_http", False)
    if not isinstance(base_url, str) or not isinstance(model, str) or (
        api_key_env is not None and not isinstance(api_key_env, str)
    ) or not isinstance(allow_insecure_http, bool):
        raise StoreError("Enter a provider base URL, model, and optional credential variable name.")
    try:
        return provider.validate(base_url, model, api_key_env, allow_insecure_http)
    except StoreError as exc:
        if "--allow-insecure-http" in str(exc):
            raise StoreError("Remote HTTP needs Allow remote HTTP. Use HTTPS when available.") from exc
        raise


def _provider_test_hint(error: EmbeddingError) -> str:
    detail = str(error)
    if "credential is missing" in detail:
        return "Set that environment variable before starting the dashboard and MCP client, then restart the dashboard and test again."
    if "HTTP 401" in detail or "HTTP 403" in detail:
        return "Check the credential variable, model, and provider URL."
    if "HTTP 5" in detail:
        return "The provider is unavailable. Check its service or gateway, then retry."
    if "could not be reached" in detail:
        return "Check that the gateway is running or that this computer can reach the provider URL."
    return "Check that this URL and model support the OpenAI-compatible /embeddings route."


class DashboardServer(ThreadingHTTPServer):
    """One local HTTP surface backed by the same store as CLI and MCP."""

    daemon_threads = True

    def __init__(self, store: SidecarStore | None = None, port: int = 8765, watch_interval: float = 1.0):
        if not 0 <= port <= 65535:
            raise StoreError("Dashboard port must be between 0 and 65535.")
        self.store = store or SidecarStore()
        self.evidence = EvidenceStore(self.store)
        self.provider = ProviderStore(self.store)
        self.csrf_token = secrets.token_urlsafe(32)
        self.operation_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: set[str] = set()
        self._errors: dict[str, str] = {}
        self._stop = threading.Event()
        self.watcher = SidecarWatcher(self.store, interval=watch_interval, update_lock=self.operation_lock)
        self._watch_thread = threading.Thread(target=self.watcher.run, args=(self._stop, self._watch_event), daemon=True)
        try:
            super().__init__(("127.0.0.1", port), DashboardHandler)
        except OSError as exc:
            raise StoreError(f"Cannot start local dashboard: {exc}") from exc

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def start(self) -> None:
        if not self._watch_thread.is_alive():
            self._watch_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._watch_thread.is_alive():
            self._watch_thread.join(timeout=5)
        self.server_close()

    def _watch_event(self, event: WatchEvent) -> None:
        if event.sidecar_id is None:
            print(f"Dashboard watcher: {event.error}", file=sys.stderr)
        elif event.error:
            with self._pending_lock:
                self._errors[event.sidecar_id] = event.error
        else:
            with self._pending_lock:
                self._errors.pop(event.sidecar_id, None)

    def schedule_update(self, sidecar_id: str) -> bool:
        sidecar = self.store.get(sidecar_id)
        if sidecar.id != sidecar_id:
            raise StoreError("Sidecar ID is invalid.")
        with self._pending_lock:
            if sidecar_id in self._pending:
                return False
            self._pending.add(sidecar_id)
            self._errors.pop(sidecar_id, None)

        def worker() -> None:
            try:
                with self.operation_lock:
                    self.evidence.update(sidecar_id)
                with self._pending_lock:
                    self._errors.pop(sidecar_id, None)
            except StoreError as exc:
                with self._pending_lock:
                    self._errors[sidecar_id] = str(exc)
            except Exception as exc:
                with self._pending_lock:
                    self._errors[sidecar_id] = f"Update failed ({type(exc).__name__}). Retry the update."
            finally:
                with self._pending_lock:
                    self._pending.discard(sidecar_id)

        threading.Thread(target=worker, daemon=True, name=f"psydcar-update-{sidecar_id[:8]}").start()
        return True

    def snapshot(self) -> dict[str, Any]:
        sidecars = self.store.list()
        provider = _provider_view(self.provider.get())
        provider_configured = provider["configured"]
        with self._pending_lock:
            queued = set(self._pending)
            errors = dict(self._errors)
        entries = []
        for sidecar in sidecars:
            update = self.evidence.update_state(sidecar.id)
            coverage = self.evidence.coverage(sidecar.id)
            embedding = self.provider.sidecar_state(sidecar.id) if provider_configured else None
            source_drift = self.watcher.source_changed(sidecar)
            state = "pending" if sidecar.id in queued else update["state"]
            freshness = (
                "not_indexed" if state == "not_indexed" else
                "stale" if state != "current" or source_drift else "current"
            )
            entries.append({
                "id": sidecar.id, "name": sidecar.name, "source_path": str(sidecar.source_path),
                "status": sidecar.status, "state": state, "freshness": freshness,
                "source_drift": source_drift, "created_at": sidecar.created_at,
                "updated_at": sidecar.updated_at, "update": update, "coverage": coverage,
                "embedding": embedding, "queued": sidecar.id in queued,
                "error": errors.get(sidecar.id),
                "pdf_pages": self.evidence.pdf_page_notices(sidecar.id),
            })
        return {"sidecars": entries, "watching": self._watch_thread.is_alive(),
                "provider_configured": provider_configured, "provider": provider}

    def connection_preview(self, name: Any, references: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not _CONNECTION_NAME.fullmatch(name):
            raise StoreError("Connection name must start with a letter and use up to 64 letters, numbers, dashes, or underscores.")
        if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
            raise StoreError("Select one or more sidecars for this connection.")
        ids = selected_ids(self.store, references)
        if importlib.util.find_spec("mcp") is None:
            raise StoreError("MCP support is unavailable in this Python. Install Psydcar with its dependencies, then reopen the dashboard.")
        args = ["-m", "psydcar", "mcp", *(part for sidecar_id in ids for part in ("--sidecar-id", sidecar_id))]
        entry = {"command": sys.executable, "args": args, "env": {"PSYDCAR_HOME": str(self.store.db_path.parent)}}
        return {
            "name": name,
            "selected": [{"id": sidecar_id, "name": self.store.get(sidecar_id).name} for sidecar_id in ids],
            "entry": entry,
            "config": {"mcpServers": {name: entry}},
        }


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _valid_host(self) -> bool:
        if self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}":
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "Open the dashboard through its local 127.0.0.1 address."})
        return False

    def _mutation_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.url:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Request origin does not match the local dashboard."})
            return False
        token = self.headers.get("X-Psydcar-Token", "")
        if not hmac.compare_digest(token, self.server.csrf_token):
            self._json(HTTPStatus.FORBIDDEN, {"error": "Dashboard session token is missing or invalid. Reload the page."})
            return False
        return True

    def _body(self) -> dict[str, Any] | None:
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send JSON for dashboard changes."})
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if not 0 <= length <= 16384:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Dashboard request is too large or has no valid length."})
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid UTF-8 JSON."})
            return None
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be a JSON object."})
            return None
        return payload

    def do_GET(self) -> None:
        if not self._valid_host():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/bootstrap":
                self._json(HTTPStatus.OK, {**self.server.snapshot(), "token": self.server.csrf_token})
                return
            if path == "/api/sidecars":
                self._json(HTTPStatus.OK, self.server.snapshot())
                return
            if path in _ASSETS:
                filename, content_type = _ASSETS[path]
                body = files("psydcar").joinpath("dashboard_assets", filename).read_bytes()
                self._headers(HTTPStatus.OK, content_type, len(body))
                self.wfile.write(body)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})
        except StoreError as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:
        if not self._valid_host() or not self._mutation_allowed():
            return
        payload = self._body()
        if payload is None:
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/provider/test":
                draft = _provider_draft(self.server.provider, payload)
                try:
                    vectors = EmbeddingClient(draft, timeout=6).embed(["Psydcar provider connection test."])
                except EmbeddingError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "hint": _provider_test_hint(exc)})
                    return
                self._json(HTTPStatus.OK, {"message": "Provider responded to a sample-text embedding request.",
                                           "dimensions": len(vectors[0])})
                return
            if path == "/api/provider":
                draft = _provider_draft(self.server.provider, payload)
                with self.server.operation_lock:
                    config = self.server.provider.set(draft.base_url, draft.model, draft.api_key_env,
                                                      draft.allow_insecure_http)
                self._json(HTTPStatus.OK, {"provider": _provider_view(config),
                                           "message": "Provider saved. Update sidecars to build embeddings with these settings."})
                return
            if path == "/api/connections/preview":
                preview = self.server.connection_preview(payload.get("name"), payload.get("sidecar_ids"))
                self._json(HTTPStatus.OK, preview)
                return
            if path == "/api/sidecars":
                name, source = payload.get("name"), payload.get("source")
                if not isinstance(name, str) or not isinstance(source, str):
                    raise StoreError("Enter a sidecar name and an existing source directory.")
                with self.server.operation_lock:
                    sidecar = self.server.store.create(name, source)
                self.server.schedule_update(sidecar.id)
                self._json(HTTPStatus.CREATED, {"id": sidecar.id, "message": f'Created "{sidecar.name}". Indexing has started.'})
                return
            match = _SIDECAR_ACTION.fullmatch(path)
            if match:
                sidecar_id, action = match.groups()
                if action == "rename":
                    name = payload.get("name")
                    if not isinstance(name, str):
                        raise StoreError("Enter a new sidecar name.")
                    with self.server.operation_lock:
                        sidecar = self.server.store.rename(sidecar_id, name)
                    self._json(HTTPStatus.OK, {"id": sidecar.id, "message": f'Renamed sidecar to "{sidecar.name}".'})
                    return
                queued = self.server.schedule_update(sidecar_id)
                self._json(HTTPStatus.ACCEPTED, {"id": sidecar_id, "message": "Update queued." if queued else "Update already queued."})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Action not found."})
        except StoreError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_DELETE(self) -> None:
        if not self._valid_host() or not self._mutation_allowed():
            return
        payload = self._body()
        if payload is None:
            return
        if urlsplit(self.path).path == "/api/provider":
            if payload.get("confirm") != "clear":
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Confirm provider removal before clearing its stored embeddings."})
                return
            try:
                with self.server.operation_lock:
                    self.server.provider.clear()
                self._json(HTTPStatus.OK, {"provider": _provider_view(None),
                                           "message": "Provider removed. Lexical evidence remains available."})
            except StoreError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        match = _SIDECAR_ITEM.fullmatch(urlsplit(self.path).path)
        if match is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Sidecar not found."})
            return
        sidecar_id = match.group(1)
        if payload.get("confirm_id") != sidecar_id:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Confirm the sidecar ID before removing it."})
            return
        try:
            with self.server.operation_lock:
                sidecar = self.server.store.remove(sidecar_id)
            self._json(HTTPStatus.OK, {"message": f'Removed "{sidecar.name}". Source files were not changed.'})
        except StoreError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def run_dashboard(store: SidecarStore | None = None, port: int = 8765, open_browser: bool = True) -> None:
    server = DashboardServer(store, port)
    server.start()
    print(f"Dashboard: {server.url}", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    prior_handler = signal.getsignal(signal.SIGTERM)

    def stop_on_term(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_term)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, prior_handler)
        server.stop()
