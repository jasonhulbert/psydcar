"""Confirmed, durable OCR for images and PDF pages."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .embeddings import _user_agent
from .index_tables import open_search
from .pdf_text import PdfTextError, extract_pdf_pages
from .registry import Sidecar, SidecarStore, StoreError
from .source_scope import MAX_PDF_BYTES, is_link, is_pdf, is_source_root, read_bytes, walk
from .storage import Tables, connect, exclusive_file_lock
from .vision import VisionProviderConfig, VisionProviderStore

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_PDF_RENDER_LOCK = threading.Lock()
_TABLES = Tables(
    "ocr",
    "sidecar_ocr",
    (
        (
            """CREATE TABLE sidecar_ocr (
            sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
            enabled_at TEXT NOT NULL
        )""",
            """CREATE TABLE ocr_images (
            sidecar_id TEXT NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
            relative_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            text TEXT NOT NULL,
            recognized_at TEXT NOT NULL,
            PRIMARY KEY (sidecar_id, relative_path)
        )""",
        ),
        (
            """CREATE TABLE ocr_progress (
            sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            current_path TEXT,
            issues_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )""",
        ),
        (
            """CREATE TABLE sidecar_pdf_ocr (
            sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
            enabled_at TEXT NOT NULL
        )""",
            """CREATE TABLE ocr_pdf_pages (
            sidecar_id TEXT NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
            relative_path TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            text TEXT NOT NULL,
            recognized_at TEXT NOT NULL,
            PRIMARY KEY (sidecar_id, relative_path, page_number)
        )""",
        ),
        (
            """CREATE TABLE sidecar_full_pdf_ocr (
            sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
            enabled_at TEXT NOT NULL
        )""",
        ),
        (
            "ALTER TABLE sidecar_ocr ADD COLUMN provider_revision TEXT",
            "ALTER TABLE sidecar_ocr ADD COLUMN grant_token TEXT",
        ),
    ),
)


def image_scope(sidecar: Sidecar) -> list[Path]:
    """Find eligible files without following links or entering ignored directories."""
    root = sidecar.source_path
    if not is_source_root(root):
        raise StoreError(f"Source directory is missing, a symbolic link, or not a directory: {root}")
    errors: list[str] = []

    def onerror(error: OSError) -> None:
        errors.append(f"{error.filename or root}: {error.strerror or error}")

    images = []
    for path, linked_directory in walk(root, onerror, include_images=True):
        if linked_directory or path.suffix.lower() not in IMAGE_TYPES or is_link(path):
            continue
        try:
            if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES:
                images.append(path.relative_to(root))
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror or exc}")
    if errors:
        raise StoreError(f"Cannot assess image scope: {errors[0]}")
    return images


def pdf_scope_details(sidecar: Sidecar) -> tuple[list[tuple[Path, int, str]], list[tuple[Path, int, str]], list[str]]:
    """Identify textless and already-searchable pages, and report scope issues."""
    root = sidecar.source_path
    if not is_source_root(root):
        raise StoreError(f"Source directory is missing, a symbolic link, or not a directory: {root}")
    candidates: list[tuple[Path, int, str]] = []
    searchable: list[tuple[Path, int, str]] = []
    issues: list[str] = []

    def onerror(error: OSError) -> None:
        issues.append(f"{error.filename or root}: {error.strerror or error}")

    for path, linked_directory in walk(root, onerror):
        if linked_directory or not is_pdf(path) or is_link(path):
            continue
        relative = path.relative_to(root)
        try:
            if path.stat().st_size > MAX_PDF_BYTES:
                issues.append(f"{relative}: exceeds the 50 MiB PDF limit")
                continue
            data, _, reason = read_bytes(path, root)
            if data is None:
                issues.append(f"{relative}: {reason}")
                continue
            text_pages, pages, errors = extract_pdf_pages(data)
            digest = hashlib.sha256(data).hexdigest()
            candidates.extend((relative, number, digest) for number in pages)
            searchable.extend((relative, page.number, digest) for page in text_pages)
            issues.extend(f"{relative}, page {number}: text extraction failed: {reason}" for number, reason in errors)
        except (OSError, PdfTextError) as exc:
            issues.append(f"{relative}: {exc}")
    return candidates, searchable, issues


def pdf_scope(sidecar: Sidecar, *, full: bool = False) -> tuple[list[tuple[Path, int, str]], list[str]]:
    """Identify eligible PDF pages for the sidecar's confirmed coverage."""
    candidates, searchable, issues = pdf_scope_details(sidecar)
    return sorted(candidates + searchable if full else candidates), issues


def render_pdf_page(data: bytes, page_number: int) -> bytes:
    """Render one PDF page locally to a bounded PNG image for the vision provider."""
    try:
        import pypdfium2 as pdfium

        with _PDF_RENDER_LOCK, pdfium.PdfDocument(data) as document:
            page = document[page_number - 1]
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    raise ValueError("Invalid page dimensions")
                scale = min(2.0, 2500 / max(width, height))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    output = BytesIO()
                    image.save(output, format="PNG")
                    return output.getvalue()
                finally:
                    bitmap.close()
            finally:
                page.close()
    except Exception as exc:
        raise StoreError(f"Cannot render PDF page {page_number}: {exc}") from exc


def _recognize(config: VisionProviderConfig, data: bytes, mime: str) -> str:
    secret = os.environ.get(config.api_key_env) if config.api_key_env else None
    if config.api_key_env and not secret:
        raise StoreError(f"OCR credential is missing from environment variable {config.api_key_env}.")
    headers = {"Content-Type": "application/json", "User-Agent": _user_agent()}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    encoded = base64.b64encode(data).decode("ascii")
    payload = json.dumps(
        {
            "model": config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Transcribe all visible text in this image. Return only the text, preserving its reading order. "
                                "If there is no text, return an empty response."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    ],
                }
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(config.chat_completions_url, data=payload, headers=headers, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=90) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise StoreError(f"OCR provider returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StoreError("OCR provider could not be reached or timed out.") from exc
    if len(raw) > 4 * 1024 * 1024:
        raise StoreError("OCR provider response is too large.")
    try:
        content = json.loads(raw)["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError
        return content.strip()
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise StoreError("OCR provider returned an invalid response.") from exc


def sync_index(connection: sqlite3.Connection, sidecar_id: str, records: list[dict[str, str]]) -> bool:
    """Rebuild image entries from the durable OCR cache without calling a provider."""
    # Import lazily to avoid a module cycle: evidence owns chunking.
    from .evidence import _chunks

    changed = False
    for record in records:
        previous = connection.execute(
            "SELECT id, sha256, indexed_at FROM documents WHERE sidecar_id = ? AND relative_path = ? AND kind = 'image_ocr'",
            (sidecar_id, record["relative_path"]),
        ).fetchone()
        # An update may have read the cache before a concurrent OCR run saved a
        # newer result. Do not let that older snapshot replace the newer entry.
        if previous is not None and previous["indexed_at"] >= record["recognized_at"]:
            continue
        if previous is not None:
            connection.execute("DELETE FROM documents WHERE id = ?", (previous["id"],))
        cursor = connection.execute(
            "INSERT INTO documents (sidecar_id, relative_path, sha256, indexed_at, kind) VALUES (?, ?, ?, ?, 'image_ocr')",
            (sidecar_id, record["relative_path"], record["sha256"], record["recognized_at"]),
        )
        for start, end, context in _chunks(record["text"]):
            evidence_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO passages (id, document_id, start_line, end_line, context, page_number) VALUES (?, ?, ?, ?, ?, NULL)",
                (evidence_id, cursor.lastrowid, start, end, context),
            )
            connection.execute("INSERT INTO passages_fts (evidence_id, context) VALUES (?, ?)", (evidence_id, context))
        changed = True
    if changed:
        connection.execute("DELETE FROM sidecar_embedding_state WHERE sidecar_id = ?", (sidecar_id,))
    return changed


def sync_pdf_index(connection: sqlite3.Connection, sidecar_id: str, records: list[dict[str, Any]]) -> bool:
    """Attach durable PDF OCR to the matching text-layer document."""
    from .evidence import _chunks

    changed = False
    for record in records:
        doc = connection.execute(
            "SELECT id, sha256 FROM documents WHERE sidecar_id = ? AND relative_path = ? AND kind = 'pdf'",
            (sidecar_id, record["relative_path"]),
        ).fetchone()
        if doc is None or doc["sha256"] != record["sha256"]:
            continue
        prior = connection.execute(
            "SELECT recognized_at FROM pdf_ocr_index WHERE document_id = ? AND page_number = ?",
            (doc["id"], record["page_number"]),
        ).fetchone()
        if prior is not None and prior["recognized_at"] >= record["recognized_at"]:
            continue
        connection.execute(
            "DELETE FROM passages WHERE document_id = ? AND page_number = ? AND extraction = 'ocr'",
            (doc["id"], record["page_number"]),
        )
        for start, end, context in _chunks(record["text"]):
            evidence_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO passages (id, document_id, start_line, end_line, context, page_number, extraction) VALUES (?, ?, ?, ?, ?, ?, 'ocr')",
                (evidence_id, doc["id"], start, end, context, record["page_number"]),
            )
            connection.execute("INSERT INTO passages_fts (evidence_id, context) VALUES (?, ?)", (evidence_id, context))
        connection.execute(
            "INSERT OR REPLACE INTO pdf_ocr_index (document_id, page_number, recognized_at) VALUES (?, ?, ?)",
            (doc["id"], record["page_number"], record["recognized_at"]),
        )
        connection.execute(
            "DELETE FROM pdf_pages WHERE document_id = ? AND page_number = ? AND status = 'no_text'", (doc["id"], record["page_number"])
        )
        changed = True
    if changed:
        connection.execute("DELETE FROM sidecar_embedding_state WHERE sidecar_id = ?", (sidecar_id,))
    return changed


class ImageOCR:
    def __init__(self, sidecars: SidecarStore):
        self.sidecars = sidecars

    def records(self, sidecar_id: str) -> list[dict[str, str]]:
        try:
            with connect(self.sidecars.db_path, _TABLES) as connection:
                return [dict(row) for row in connection.execute("SELECT * FROM ocr_images WHERE sidecar_id = ?", (sidecar_id,))]
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(f"Cannot inspect saved OCR: {exc}") from exc

    def enabled(self, sidecar_id: str) -> bool:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            return connection.execute("SELECT 1 FROM sidecar_ocr WHERE sidecar_id = ?", (sidecar_id,)).fetchone() is not None

    def authorization(self, sidecar_id: str) -> tuple[str | None, str | None] | None:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            row = connection.execute("SELECT provider_revision, grant_token FROM sidecar_ocr WHERE sidecar_id = ?", (sidecar_id,)).fetchone()
            return (row["provider_revision"], row["grant_token"]) if row else None

    def disable(self, sidecar_id: str) -> None:
        """Stop future OCR requests and retain recognized text for search and reuse."""
        with connect(self.sidecars.db_path, _TABLES) as connection:
            connection.execute("DELETE FROM sidecar_full_pdf_ocr WHERE sidecar_id = ?", (sidecar_id,))
            connection.execute("DELETE FROM sidecar_pdf_ocr WHERE sidecar_id = ?", (sidecar_id,))
            connection.execute("DELETE FROM sidecar_ocr WHERE sidecar_id = ?", (sidecar_id,))
            connection.execute("DELETE FROM ocr_progress WHERE sidecar_id = ?", (sidecar_id,))

    def needs_confirmation(self, sidecar_id: str, config: VisionProviderConfig | None) -> bool:
        authorization = self.authorization(sidecar_id)
        return authorization is not None and config is not None and authorization[0] != config.revision

    def pdf_enabled(self, sidecar_id: str) -> bool:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            return connection.execute("SELECT 1 FROM sidecar_pdf_ocr WHERE sidecar_id = ?", (sidecar_id,)).fetchone() is not None

    def full_pdf_enabled(self, sidecar_id: str) -> bool:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            return connection.execute("SELECT 1 FROM sidecar_full_pdf_ocr WHERE sidecar_id = ?", (sidecar_id,)).fetchone() is not None

    def pdf_records(self, sidecar_id: str) -> list[dict[str, Any]]:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM ocr_pdf_pages WHERE sidecar_id = ?", (sidecar_id,))]

    def status(self, sidecar_id: str) -> dict[str, Any] | None:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            enabled = connection.execute("SELECT 1 FROM sidecar_ocr WHERE sidecar_id = ?", (sidecar_id,)).fetchone()
            if not enabled:
                return None
            row = connection.execute("SELECT * FROM ocr_progress WHERE sidecar_id = ?", (sidecar_id,)).fetchone()
        if row is None:
            return {
                "status": "pending",
                "total": 0,
                "completed": 0,
                "failed": 0,
                "current_path": None,
                "issues": [],
                "pdf_enabled": self.pdf_enabled(sidecar_id),
                "full_pdf_enabled": self.full_pdf_enabled(sidecar_id),
                "provider_changed": self.needs_confirmation(sidecar_id, VisionProviderStore(self.sidecars).get()),
            }
        result = dict(row)
        result["issues"] = json.loads(result.pop("issues_json"))
        result["pdf_enabled"] = self.pdf_enabled(sidecar_id)
        result["full_pdf_enabled"] = self.full_pdf_enabled(sidecar_id)
        result["provider_changed"] = self.needs_confirmation(sidecar_id, VisionProviderStore(self.sidecars).get())
        if result["status"] == "processing":
            try:
                with exclusive_file_lock(self.sidecars.db_path.parent / f".ocr-{sidecar_id}.lock", wait=False):
                    result["status"] = "incomplete"
                    result["failed"] = max(1, result["total"] - result["completed"])
                    result["issues"].append("OCR was interrupted. Run Update to resume.")
            except BlockingIOError:
                pass
        return result

    def _progress(self, sidecar_id: str, status: str, total: int, completed: int, failed: int, current_path: str | None, issues: list[str]) -> None:
        with connect(self.sidecars.db_path, _TABLES) as connection:
            connection.execute(
                """INSERT INTO ocr_progress (sidecar_id, status, total, completed, failed, current_path, issues_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sidecar_id) DO UPDATE SET
                   status=excluded.status, total=excluded.total, completed=excluded.completed, failed=excluded.failed,
                   current_path=excluded.current_path, issues_json=excluded.issues_json, updated_at=excluded.updated_at""",
                (
                    sidecar_id,
                    status,
                    total,
                    completed,
                    failed,
                    current_path,
                    json.dumps(issues[:20]),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def enable_and_process(
        self,
        sidecar: Sidecar,
        config: VisionProviderConfig,
        images: list[Path],
        pdf_pages: list[tuple[Path, int, str]],
        scope_issues: list[str],
    ) -> tuple[int, int, list[str]]:
        """Authorize image and default PDF OCR only after confirmation."""
        authorization = self.authorize(sidecar, config)
        return self.process_authorized(sidecar, config, images, pdf_pages, scope_issues, authorization)

    def authorize(self, sidecar: Sidecar, config: VisionProviderConfig, *, full_pdf: bool = False) -> tuple[str, str]:
        current = VisionProviderStore(self.sidecars).get()
        if current is None or current.revision != config.revision:
            raise StoreError("The OCR provider changed. Review the scope and destination again.")
        with connect(self.sidecars.db_path, _TABLES) as connection:
            now = datetime.now(UTC).isoformat(timespec="seconds")
            token = str(uuid.uuid4())
            if full_pdf and connection.execute("SELECT 1 FROM sidecar_pdf_ocr WHERE sidecar_id = ?", (sidecar.id,)).fetchone() is None:
                raise StoreError("Enable default sidecar OCR before reviewing full PDF scans.")
            connection.execute(
                """INSERT INTO sidecar_ocr (sidecar_id, enabled_at, provider_revision, grant_token) VALUES (?, ?, ?, ?)
                ON CONFLICT(sidecar_id) DO UPDATE SET enabled_at=excluded.enabled_at,
                provider_revision=excluded.provider_revision, grant_token=excluded.grant_token""",
                (sidecar.id, now, config.revision, token),
            )
            connection.execute("INSERT OR IGNORE INTO sidecar_pdf_ocr (sidecar_id, enabled_at) VALUES (?, ?)", (sidecar.id, now))
            if full_pdf:
                connection.execute("INSERT OR IGNORE INTO sidecar_full_pdf_ocr (sidecar_id, enabled_at) VALUES (?, ?)", (sidecar.id, now))
        return config.revision, token

    def enable_full_and_process(
        self,
        sidecar: Sidecar,
        config: VisionProviderConfig,
        images: list[Path],
        pdf_pages: list[tuple[Path, int, str]],
        scope_issues: list[str],
    ) -> tuple[int, int, list[str]]:
        """Authorize full PDF OCR only after its separate confirmation."""
        authorization = self.authorize(sidecar, config, full_pdf=True)
        return self.process_authorized(sidecar, config, images, pdf_pages, scope_issues, authorization)

    def update(self, sidecar: Sidecar) -> dict[str, Any] | None:
        """Refresh confirmed OCR, reusing saved results for unchanged bytes."""
        if not self.enabled(sidecar.id):
            return None
        authorization = self.authorization(sidecar.id)
        config = VisionProviderStore(self.sidecars).get()
        if self.needs_confirmation(sidecar.id, config):
            previous = self.status(sidecar.id)
            total = previous["total"] if previous else 0
            completed = previous["completed"] if previous else 0
            self._progress(
                sidecar.id,
                "incomplete",
                total,
                completed,
                1,
                None,
                ["OCR provider settings changed. Review the destination and confirm OCR for this sidecar again."],
            )
            return self.status(sidecar.id)
        try:
            images = image_scope(sidecar)
            pdf_pages, scope_issues = pdf_scope(sidecar, full=self.full_pdf_enabled(sidecar.id)) if self.pdf_enabled(sidecar.id) else ([], [])
        except StoreError as exc:
            self._progress(sidecar.id, "incomplete", 0, 0, 1, None, [str(exc)])
            return self.status(sidecar.id)
        try:
            self.process_authorized(sidecar, config, images, pdf_pages, scope_issues, authorization)
        except StoreError as exc:
            self._progress(sidecar.id, "incomplete", len(images) + len(pdf_pages), 0, 1, None, [str(exc)])
        return self.status(sidecar.id)

    def process_authorized(
        self,
        sidecar: Sidecar,
        config: VisionProviderConfig | None,
        images: list[Path],
        pdf_pages: list[tuple[Path, int, str]],
        scope_issues: list[str],
        authorization: tuple[str | None, str | None] | None,
    ) -> tuple[int, int, list[str]]:
        lock = self.sidecars.db_path.parent / f".ocr-{sidecar.id}.lock"
        try:
            with exclusive_file_lock(lock):
                if authorization is None or self.authorization(sidecar.id) != authorization:
                    return 0, 0, ["OCR was turned off."]
                saved = {row["relative_path"]: row for row in self.records(sidecar.id)}
                saved_pages = {(row["relative_path"], row["page_number"]): row for row in self.pdf_records(sidecar.id)}
                completed = reused = 0
                failed = len(scope_issues)
                issues = list(scope_issues)
                items: list[tuple[Path, int | None, str | None]] = [(path, None, None) for path in images]
                items.extend(pdf_pages)
                total = len(items) + failed
                last_pdf: Path | None = None
                pdf_data: bytes | None = None
                pdf_reason: str | None = None
                pdf_digest: str | None = None
                self._progress(sidecar.id, "processing", total, 0, failed, None, issues)
                for index, (relative, page_number, assessed_digest) in enumerate(items):
                    label = f"{relative}, page {page_number}" if page_number else str(relative)
                    self._progress(sidecar.id, "processing", total, completed + reused, failed, label, issues)
                    path = sidecar.source_path / relative
                    if page_number and relative == last_pdf:
                        data, reason = pdf_data, pdf_reason
                    else:
                        data, _, reason = read_bytes(path, sidecar.source_path)
                        if page_number:
                            last_pdf, pdf_data, pdf_reason = relative, data, reason
                            pdf_digest = hashlib.sha256(data).hexdigest() if data is not None else None
                    limit = MAX_PDF_BYTES if page_number else MAX_IMAGE_BYTES
                    if data is None or len(data) > limit:
                        failed += 1
                        issues.append(f"{label}: {reason or 'exceeds the size limit'}")
                        continue
                    digest = pdf_digest if page_number else hashlib.sha256(data).hexdigest()
                    assert digest is not None
                    if page_number and assessed_digest != digest:
                        failed += 1
                        issues.append(f"{label}: PDF changed since scope assessment; update to retry.")
                        continue
                    previous = saved_pages.get((str(relative), page_number)) if page_number else saved.get(str(relative))
                    if previous is not None and previous["sha256"] == digest:
                        reused += 1
                        continue
                    if config is None:
                        issues.append(f"{label}: Configure an OCR vision provider and update to retry. Remaining items were not processed.")
                        failed += len(items) - index
                        break
                    if self.authorization(sidecar.id) != authorization:
                        issues.append("OCR authorization changed. Remaining items were not processed.")
                        failed += len(items) - index
                        break
                    current_provider = VisionProviderStore(self.sidecars).get()
                    if current_provider is None or current_provider.revision != config.revision or authorization[0] != config.revision:
                        issues.append("OCR provider changed. Review the destination and confirm OCR for this sidecar again.")
                        failed += len(items) - index
                        break
                    try:
                        image = render_pdf_page(data, page_number) if page_number else data
                        mime = "image/png" if page_number else IMAGE_TYPES[path.suffix.lower()]
                        if self.authorization(sidecar.id) != authorization:
                            issues.append("OCR authorization changed. Remaining items were not processed.")
                            failed += len(items) - index
                            break
                        current_provider = VisionProviderStore(self.sidecars).get()
                        if current_provider is None or current_provider.revision != config.revision:
                            issues.append("OCR provider changed. Review the destination and confirm OCR for this sidecar again.")
                            failed += len(items) - index
                            break
                        recognized = _recognize(config, image, mime)
                    except StoreError as exc:
                        issues.append(f"{label}: {exc} Remaining items were not processed; update to retry.")
                        failed += len(items) - index
                        break
                    now = datetime.now(UTC).isoformat(timespec="microseconds")
                    with connect(self.sidecars.db_path, _TABLES) as connection:
                        if page_number:
                            connection.execute(
                                """INSERT INTO ocr_pdf_pages (sidecar_id, relative_path, page_number, sha256, text, recognized_at)
                                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(sidecar_id, relative_path, page_number) DO UPDATE SET
                                   sha256=excluded.sha256, text=excluded.text, recognized_at=excluded.recognized_at""",
                                (sidecar.id, str(relative), page_number, digest, recognized, now),
                            )
                        else:
                            connection.execute(
                                """INSERT INTO ocr_images (sidecar_id, relative_path, sha256, text, recognized_at) VALUES (?, ?, ?, ?, ?)
                                   ON CONFLICT(sidecar_id, relative_path) DO UPDATE SET
                                   sha256=excluded.sha256, text=excluded.text, recognized_at=excluded.recognized_at""",
                                (sidecar.id, str(relative), digest, recognized, now),
                            )
                    completed += 1
                    if page_number:
                        self.sync(
                            sidecar.id,
                            [],
                            [
                                {
                                    "relative_path": str(relative),
                                    "page_number": page_number,
                                    "sha256": digest,
                                    "text": recognized,
                                    "recognized_at": now,
                                }
                            ],
                        )
                    else:
                        self.sync(sidecar.id, [{"relative_path": str(relative), "sha256": digest, "text": recognized, "recognized_at": now}], [])
                self.sync(sidecar.id)
                self._progress(sidecar.id, "incomplete" if failed else "complete", total, completed + reused, failed, None, issues)
                return completed, reused, issues
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(f"Cannot save OCR results: {exc}") from exc

    def sync(self, sidecar_id: str, records: list[dict[str, str]] | None = None, pdf_records: list[dict[str, Any]] | None = None) -> None:
        if records is None:
            records = self.records(sidecar_id)
        if pdf_records is None:
            pdf_records = self.pdf_records(sidecar_id)
        with open_search(self.sidecars) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if sidecar_id not in self.sidecars.ids():
                raise StoreError("This sidecar was removed during OCR processing.")
            connection.execute(
                "INSERT OR IGNORE INTO sidecar_index (id, status, updated_at) VALUES (?, 'not_indexed', ?)",
                (sidecar_id, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            sync_index(connection, sidecar_id, records)
            sync_pdf_index(connection, sidecar_id, pdf_records)
