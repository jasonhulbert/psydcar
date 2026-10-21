"""Explicit OpenAI-compatible embedding configuration and local vector storage."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .store import SidecarStore, StoreError


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EmbeddingError(Exception):
    """Embedding processing failed without exposing request content or credentials."""


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api_key_env: str | None
    allow_insecure_http: bool
    revision: str
    configured_at: str

    @property
    def embeddings_url(self) -> str:
        return f"{self.base_url}/embeddings"

    @property
    def credential_available(self) -> bool:
        return self.api_key_env is None or bool(os.environ.get(self.api_key_env))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_base_url(value: str, allow_insecure_http: bool) -> str:
    url = value.strip().rstrip("/")
    if any(char.isspace() or ord(char) < 32 for char in url):
        raise StoreError("Provider URL cannot contain whitespace or control characters.")
    try:
        parsed = urllib.parse.urlsplit(url)
        _ = parsed.port
    except ValueError as exc:
        raise StoreError("Provider URL has an invalid host or port.") from exc
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise StoreError("Provider URL must be an HTTP(S) base URL without credentials, query, or fragment.")
    if parsed.path.endswith("/embeddings"):
        raise StoreError("Provider URL must be the base URL; omit the /embeddings suffix.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and not allow_insecure_http:
        raise StoreError("Remote HTTP needs --allow-insecure-http. Use HTTPS when available.")
    return url


def _validate_model(value: str) -> str:
    model = value.strip()
    if not model or len(model) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in model):
        raise StoreError("Embedding model must be a nonempty name of at most 200 characters.")
    return model


def _validate_env(value: str | None) -> str | None:
    if value is not None and not _ENV_NAME.fullmatch(value):
        raise StoreError("API key environment variable must be a valid variable name.")
    return value


def _from_row(row: sqlite3.Row) -> ProviderConfig:
    return ProviderConfig(
        base_url=row["base_url"], model=row["model"], api_key_env=row["api_key_env"],
        allow_insecure_http=bool(row["allow_insecure_http"]), revision=row["revision"],
        configured_at=row["configured_at"],
    )


def _config_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS embedding_provider (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            base_url TEXT NOT NULL,
            model TEXT NOT NULL,
            api_key_env TEXT,
            allow_insecure_http INTEGER NOT NULL,
            revision TEXT NOT NULL,
            configured_at TEXT NOT NULL
        )"""
    )


def _vector_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS passage_embeddings (
            passage_id TEXT PRIMARY KEY REFERENCES passages(id) ON DELETE CASCADE,
            provider_revision TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            dimension INTEGER NOT NULL
        )"""
    )


def _state_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS sidecar_embedding_state (
            sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
            provider_revision TEXT NOT NULL,
            status TEXT NOT NULL,
            embedded_count INTEGER NOT NULL,
            expected_count INTEGER NOT NULL,
            message TEXT,
            updated_at TEXT NOT NULL
        )"""
    )


def _has_vectors_table(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'passage_embeddings'"
    ).fetchone() is not None


class ProviderStore:
    """Store one explicitly chosen processing route for this OS user."""

    def __init__(self, sidecars: SidecarStore | None = None):
        self.sidecars = sidecars or SidecarStore()

    def get(self) -> ProviderConfig | None:
        try:
            with self.sidecars._connect() as connection:
                _config_schema(connection)
                row = connection.execute("SELECT * FROM embedding_provider WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot inspect embedding provider: {exc}") from exc
        return _from_row(row) if row is not None else None

    @staticmethod
    def validate(base_url: str, model: str, api_key_env: str | None = None,
                 allow_insecure_http: bool = False) -> ProviderConfig:
        """Validate a provider draft without changing the active configuration."""
        return ProviderConfig(
            _validate_base_url(base_url, allow_insecure_http), _validate_model(model),
            _validate_env(api_key_env), allow_insecure_http, "", "",
        )

    def set(self, base_url: str, model: str, api_key_env: str | None = None, allow_insecure_http: bool = False) -> ProviderConfig:
        draft = self.validate(base_url, model, api_key_env, allow_insecure_http)
        revision = str(uuid.uuid4())
        now = _now()
        try:
            with self.sidecars._connect() as connection:
                _config_schema(connection)
                _state_schema(connection)
                connection.execute(
                    """INSERT INTO embedding_provider
                       (id, base_url, model, api_key_env, allow_insecure_http, revision, configured_at)
                       VALUES (1, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url, model=excluded.model,
                           api_key_env=excluded.api_key_env, allow_insecure_http=excluded.allow_insecure_http,
                           revision=excluded.revision, configured_at=excluded.configured_at""",
                    (draft.base_url, draft.model, draft.api_key_env, int(draft.allow_insecure_http), revision, now),
                )
                if _has_vectors_table(connection):
                    connection.execute("DELETE FROM passage_embeddings")
                connection.execute(
                    """INSERT INTO sidecar_embedding_state
                       (sidecar_id, provider_revision, status, embedded_count, expected_count, message, updated_at)
                       SELECT id, ?, 'pending', 0, 0, 'Reindex this sidecar to build embeddings.', ? FROM sidecars WHERE true
                       ON CONFLICT(sidecar_id) DO UPDATE SET provider_revision=excluded.provider_revision,
                           status='pending', embedded_count=0, expected_count=0,
                           message=excluded.message, updated_at=excluded.updated_at""",
                    (revision, now),
                )
                connection.execute("UPDATE sidecars SET status = 'embedding_pending' WHERE status IN ('indexed', 'embedding_error', 'embedding_pending')")
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot save embedding provider: {exc}") from exc
        return ProviderConfig(draft.base_url, draft.model, draft.api_key_env, draft.allow_insecure_http, revision, now)

    def clear(self) -> bool:
        try:
            with self.sidecars._connect() as connection:
                _config_schema(connection)
                _state_schema(connection)
                existed = connection.execute("DELETE FROM embedding_provider WHERE id = 1").rowcount > 0
                if _has_vectors_table(connection):
                    connection.execute("DELETE FROM passage_embeddings")
                connection.execute("DELETE FROM sidecar_embedding_state")
                connection.execute("UPDATE sidecars SET status = 'indexed' WHERE status IN ('embedding_pending', 'embedding_error')")
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot clear embedding provider: {exc}") from exc
        return existed

    def sidecar_state(self, sidecar_id: str) -> dict[str, Any] | None:
        try:
            with self.sidecars._connect() as connection:
                _state_schema(connection)
                row = connection.execute(
                    "SELECT status, embedded_count, expected_count, message FROM sidecar_embedding_state WHERE sidecar_id = ?",
                    (sidecar_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"Cannot inspect embedding status: {exc}") from exc
        if row is None:
            return None
        return {key: row[key] for key in ("status", "embedded_count", "expected_count", "message")}


def normalize_vector(values: Any) -> tuple[float, ...]:
    if not isinstance(values, list) or not values:
        raise EmbeddingError("Embedding provider returned an empty or invalid vector.")
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise EmbeddingError("Embedding provider returned a nonnumeric vector.") from exc
    if not all(math.isfinite(value) for value in vector):
        raise EmbeddingError("Embedding provider returned a nonfinite vector.")
    length = math.sqrt(sum(value * value for value in vector))
    if not length or not math.isfinite(length):
        raise EmbeddingError("Embedding provider returned a zero-length vector.")
    return tuple(value / length for value in vector)


class EmbeddingClient:
    """Small, dependency-free client for the OpenAI-compatible embeddings route."""

    def __init__(self, config: ProviderConfig, timeout: float = 30):
        self.config = config
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        secret = os.environ.get(self.config.api_key_env) if self.config.api_key_env else None
        if self.config.api_key_env and not secret:
            raise EmbeddingError(f"Embedding credential is missing from environment variable {self.config.api_key_env}.")
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        payload = json.dumps({"model": self.config.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(self.config.embeddings_url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise EmbeddingError(f"Embedding provider returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmbeddingError("Embedding provider could not be reached or timed out.") from exc
        if len(raw) > 16 * 1024 * 1024:
            raise EmbeddingError("Embedding provider response is too large.")
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EmbeddingError("Embedding provider returned a non-UTF-8 response.") from exc
        try:
            body = json.loads(decoded)
            entries = body["data"]
            if not isinstance(entries, list) or len(entries) != len(texts):
                raise ValueError
            ordered: list[tuple[float, ...] | None] = [None] * len(texts)
            for entry in entries:
                index = entry["index"]
                if not isinstance(index, int) or not 0 <= index < len(texts) or ordered[index] is not None:
                    raise ValueError
                ordered[index] = normalize_vector(entry["embedding"])
            if any(vector is None for vector in ordered):
                raise ValueError
            dimensions = {len(vector) for vector in ordered if vector is not None}
            if len(dimensions) != 1:
                raise ValueError
            return [vector for vector in ordered if vector is not None]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EmbeddingError("Embedding provider returned an invalid response.") from exc
