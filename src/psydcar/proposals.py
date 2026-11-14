"""Queue agent edits and apply them only after a local review."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import unified_diff
from typing import Any, Literal

from .proposal_files import FileConflict, Workspace, text_bytes
from .registry import SidecarStore, StoreError
from .storage import Tables, connect


MAX_PENDING = 50
MAX_SUMMARY_LENGTH = 500
_FIELDS = "id, path, agent_name, summary, status, created_at, decided_at"
_TABLES = Tables("proposals", "file_proposals", ((
    """CREATE TABLE file_proposals (
        id TEXT PRIMARY KEY,
        sidecar_id TEXT NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        summary TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'applying', 'approved', 'rejected')),
        created_at TEXT NOT NULL,
        decided_at TEXT,
        base_version TEXT NOT NULL,
        result_version TEXT NOT NULL,
        newline TEXT NOT NULL,
        bom INTEGER NOT NULL,
        before_text TEXT,
        proposed_text TEXT
    )""",
    """CREATE INDEX file_proposals_queue
       ON file_proposals(sidecar_id, status, created_at DESC, id)""",
),))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text_diff(before: str, proposed: str) -> str | None:
    # Bound the work for long files or pathological line sequences.
    if len(before) + len(proposed) > 128 * 1024:
        return None
    old_lines = before.splitlines(keepends=True)
    new_lines = proposed.splitlines(keepends=True)
    if len(old_lines) + len(new_lines) > 2500:
        return None
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in unified_diff(old_lines, new_lines, n=3)
    )


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    result = {key: row[key] for key in _FIELDS.split(", ")}
    if result["status"] == "applying":
        result["status"] = "pending"
    return result


class Proposals:
    def __init__(self, store: SidecarStore):
        self.store = store
        self.workspace = Workspace(store)

    @contextmanager
    def _state(self) -> Iterator[sqlite3.Connection]:
        # Removing a sidecar removes its proposals through the foreign key.
        try:
            with connect(self.store.db_path, _TABLES) as connection:
                yield connection
        except OSError as exc:
            raise StoreError(f"Cannot open sidecar state at {self.store.db_path}: {exc}") from exc

    def submit(self, sidecar_id: str, path: str, version: str, content: str,
               summary: str, agent_name: str) -> dict[str, Any]:
        summary = summary.strip()
        if not summary or len(summary) > MAX_SUMMARY_LENGTH or not summary.isprintable():
            raise StoreError("Describe the proposed edit in one line of up to 500 characters.")
        if "\0" in content:
            raise StoreError("A proposed edit must be UTF-8 text.")
        content = content.replace("\r\n", "\n")
        with self.workspace.edit(sidecar_id) as source:
            base = source.read(path)
            if base["version"] != version:
                raise FileConflict("The file changed. Read its current text before proposing an edit.", base["version"])
            result_version = hashlib.sha256(text_bytes(content, base["newline"], base["bom"])).hexdigest()
            if content == base["content"] or result_version == version:
                raise StoreError("The proposal must change the file.")
            with self._state() as state:
                existing = state.execute(
                    f"""SELECT {_FIELDS} FROM file_proposals
                        WHERE sidecar_id = ? AND status = 'pending' AND path = ? AND agent_name = ?
                        AND summary = ? AND base_version = ? AND result_version = ?""",
                    (sidecar_id, base["path"], agent_name, summary, version, result_version),
                ).fetchone()
                if existing is not None:
                    return _summary(existing)
                if self._pending_count(state, sidecar_id) >= MAX_PENDING:
                    raise StoreError("This sidecar's review queue is full. Ask the user to review pending edits.")
                proposal_id = str(uuid.uuid4())
                state.execute(
                    """INSERT INTO file_proposals
                       (id, sidecar_id, path, agent_name, summary, status, created_at, base_version,
                        result_version, newline, bom, before_text, proposed_text)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
                    (proposal_id, sidecar_id, base["path"], agent_name, summary, _now(), version,
                     result_version, base["newline"], base["bom"], base["content"], content),
                )
                return _summary(self._find(state, sidecar_id, proposal_id))

    @staticmethod
    def _pending_count(state: sqlite3.Connection, sidecar_id: str) -> int:
        return state.execute(
            "SELECT count(*) FROM file_proposals WHERE sidecar_id = ? AND status IN ('pending', 'applying')",
            (sidecar_id,),
        ).fetchone()[0]

    def list(self, sidecar_id: str, *, reviewed: bool = False) -> dict[str, Any]:
        self.store.get(sidecar_id, exact_id=True)
        with self._state() as state:
            if reviewed:
                rows = state.execute(
                    f"""SELECT {_FIELDS} FROM file_proposals
                        WHERE sidecar_id = ? AND status IN ('approved', 'rejected')
                        ORDER BY decided_at DESC, rowid DESC LIMIT 50""",
                    (sidecar_id,),
                ).fetchall()
            else:
                rows = state.execute(
                    f"""SELECT {_FIELDS} FROM file_proposals
                        WHERE sidecar_id = ? AND status IN ('pending', 'applying')
                        ORDER BY created_at DESC, rowid DESC""",
                    (sidecar_id,),
                ).fetchall()
            return {
                "items": [_summary(row) for row in rows],
                "pending_count": self._pending_count(state, sidecar_id) if reviewed else len(rows),
            }

    @staticmethod
    def _find(state: sqlite3.Connection, sidecar_id: str, proposal_id: str) -> sqlite3.Row:
        row = state.execute(
            "SELECT * FROM file_proposals WHERE sidecar_id = ? AND id = ?", (sidecar_id, proposal_id),
        ).fetchone()
        if row is None:
            raise StoreError("This proposal does not exist in this sidecar.")
        return row

    def _finish(self, row: sqlite3.Row, status: str) -> sqlite3.Row:
        with self._state() as state:
            state.execute(
                """UPDATE file_proposals
                   SET status = ?, decided_at = ?, before_text = NULL, proposed_text = NULL WHERE id = ?""",
                (status, _now(), row["id"]),
            )
            return self._find(state, row["sidecar_id"], row["id"])

    def _current_version(self, row: sqlite3.Row) -> str | None:
        try:
            return self.workspace.files(row["sidecar_id"]).read(row["path"])["version"]
        except (StoreError, OSError):
            return None

    def _load(self, sidecar_id: str, proposal_id: str) -> sqlite3.Row:
        with self._state() as state:
            row = self._find(state, sidecar_id, proposal_id)
        if row["status"] != "applying":
            return row
        # An approval was recorded before the atomic file replacement. Recover
        # the completed write by hash, or leave it pending for another review.
        if self._current_version(row) == row["result_version"]:
            return self._finish(row, "approved")
        with self._state() as state:
            state.execute("UPDATE file_proposals SET status = 'pending' WHERE id = ?", (proposal_id,))
            return self._find(state, sidecar_id, proposal_id)

    def detail(self, sidecar_id: str, proposal_id: str) -> dict[str, Any]:
        with self.store.file_writes():
            row = self._load(sidecar_id, proposal_id)
            stale = row["status"] == "pending" and self._current_version(row) != row["base_version"]
        before, proposed = row["before_text"], row["proposed_text"]
        diff = text_diff(before, proposed) if before is not None and proposed is not None else None
        return {
            **_summary(row), "stale": stale, "diff": diff,
            "before": before if diff is None else None,
            "proposed": proposed if diff is None else None,
        }

    def review(self, sidecar_id: str, proposal_id: str, decision: Literal["approved", "rejected"]) -> dict[str, Any]:
        if decision not in ("approved", "rejected"):
            raise StoreError("Choose whether to approve or reject the proposal.")
        with self.store.file_writes():
            row = self._load(sidecar_id, proposal_id)
            if row["status"] == decision:
                return _summary(row)
            if row["status"] != "pending":
                raise StoreError("This proposal has already been reviewed.")
            if decision == "rejected":
                return _summary(self._finish(row, decision))
            current = self._current_version(row)
            if current != row["base_version"]:
                raise FileConflict("The file changed or is unavailable. Ask the agent for a new proposal.", current)
            with self._state() as state:
                state.execute("UPDATE file_proposals SET status = 'applying' WHERE id = ?", (proposal_id,))
            self.workspace.files(sidecar_id).write(
                row["path"], row["proposed_text"], row["base_version"], row["newline"], bool(row["bom"]),
            )
            return _summary(self._finish(row, decision))
