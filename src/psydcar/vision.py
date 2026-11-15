"""Configuration for the optional OpenAI-compatible vision provider."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from .embeddings import _validate_base_url, _validate_env
from .registry import SidecarStore, StoreError
from .storage import Tables, connect

_TABLES = Tables(
    "vision provider",
    "vision_provider",
    (
        (
            """CREATE TABLE vision_provider (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        base_url TEXT NOT NULL,
        model TEXT NOT NULL,
        api_key_env TEXT,
        allow_insecure_http INTEGER NOT NULL,
        revision TEXT NOT NULL,
        configured_at TEXT NOT NULL
    )""",
        ),
    ),
)


@dataclass(frozen=True)
class VisionProviderConfig:
    base_url: str
    model: str
    api_key_env: str | None
    allow_insecure_http: bool
    revision: str
    configured_at: str

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def credential_available(self) -> bool:
        return self.api_key_env is None or bool(os.environ.get(self.api_key_env))


class VisionProviderStore:
    """Persist vision settings separately from embeddings and sidecar state."""

    def __init__(self, sidecars: SidecarStore | None = None):
        self.sidecars = sidecars or SidecarStore()

    @staticmethod
    def validate(base_url: str, model: str, api_key_env: str | None = None, allow_insecure_http: bool = False) -> VisionProviderConfig:
        base_url = _validate_base_url(base_url, allow_insecure_http)
        if base_url.endswith("/chat/completions"):
            raise StoreError("Provider URL must be the base URL; omit the /chat/completions suffix.")
        model = model.strip()
        if not model or len(model) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in model):
            raise StoreError("Vision model must be a nonempty name of at most 200 characters.")
        return VisionProviderConfig(base_url, model, _validate_env(api_key_env), allow_insecure_http, "", "")

    def get(self) -> VisionProviderConfig | None:
        try:
            with connect(self.sidecars.db_path, _TABLES) as connection:
                row = connection.execute("SELECT * FROM vision_provider WHERE id = 1").fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(f"Cannot inspect vision provider: {exc}") from exc
        if row is None:
            return None
        return VisionProviderConfig(
            row["base_url"], row["model"], row["api_key_env"], bool(row["allow_insecure_http"]), row["revision"], row["configured_at"]
        )

    def set(self, base_url: str, model: str, api_key_env: str | None = None, allow_insecure_http: bool = False) -> VisionProviderConfig:
        draft = self.validate(base_url, model, api_key_env, allow_insecure_http)
        revision = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with connect(self.sidecars.db_path, _TABLES) as connection:
                connection.execute(
                    """INSERT INTO vision_provider (id, base_url, model, api_key_env, allow_insecure_http, revision, configured_at)
                       VALUES (1, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url, model=excluded.model,
                           api_key_env=excluded.api_key_env, allow_insecure_http=excluded.allow_insecure_http,
                           revision=excluded.revision, configured_at=excluded.configured_at""",
                    (draft.base_url, draft.model, draft.api_key_env, int(draft.allow_insecure_http), revision, now),
                )
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(f"Cannot save vision provider: {exc}") from exc
        return VisionProviderConfig(draft.base_url, draft.model, draft.api_key_env, draft.allow_insecure_http, revision, now)

    def clear(self) -> None:
        try:
            with connect(self.sidecars.db_path, _TABLES) as connection:
                connection.execute("DELETE FROM vision_provider WHERE id = 1")
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(f"Cannot clear vision provider: {exc}") from exc
