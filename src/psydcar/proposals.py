"""Queue agent edits and apply them only after a local review."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .evidence import EvidenceStore, UpdateSuperseded
from .git_record import CommitPlan, GitRecord
from .proposal_files import FileConflict, LineMatcher, ProposalFiles, text_bytes
from .registry import SidecarStore, StoreError
from .storage import Tables, connect

MAX_PENDING = 50
MAX_SUMMARY_LENGTH = 500
MIN_ID_PREFIX = 4
_ID_PREFIX = re.compile(r"[0-9a-f-]+")
_FIELDS = "id, path, agent_name, summary, status, created_at, decided_at, commit_id"
_TABLES = Tables(
    "proposals",
    "file_proposals",
    (
        (
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
        ),
        (
            # The commit that recorded an approval, when the sidecar's git record was on.
            "ALTER TABLE file_proposals ADD COLUMN commit_id TEXT",
        ),
    ),
)


def _now() -> str:
    # Milliseconds keep decisions made within one second in order.
    return datetime.now(UTC).isoformat(timespec="milliseconds")


_DIFF_CONTEXT = 3


def _unified_range(start: int, stop: int) -> str:
    length = stop - start
    if length == 1:
        return str(start + 1)
    return f"{start + 1 if length else start},{length}"


def text_diff(before: str, proposed: str, path: str) -> str:
    """Return a unified diff of a proposal, with paths relative to the sidecar folder."""
    old = before.splitlines(keepends=True)
    new = proposed.splitlines(keepends=True)
    lines = [f"--- a/{path}\n", f"+++ b/{path}\n"]
    for group in LineMatcher(old, new).get_grouped_opcodes(_DIFF_CONTEXT):
        lines.append(f"@@ -{_unified_range(group[0][1], group[-1][2])} +{_unified_range(group[0][3], group[-1][4])} @@\n")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(" " + line for line in old[i1:i2])
                continue
            lines.extend("-" + line for line in old[i1:i2])
            lines.extend("+" + line for line in new[j1:j2])
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def _conflict(row: sqlite3.Row, current: str | None) -> FileConflict:
    path, agent = row["path"], row["agent_name"]
    if current is None:
        return FileConflict(f"{path} was moved or deleted, or can't be read. Ask {agent} for a new proposal.", None)
    return FileConflict(f"{path} changed after {agent} read it. Ask {agent} for a new proposal.", current)


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    result: dict[str, Any] = {key: row[key] for key in _FIELDS.split(", ")}
    if result["status"] == "applying":
        result["status"] = "pending"
    return result


class Proposals:
    def __init__(self, store: SidecarStore):
        self.store = store
        self.sources = ProposalFiles(store)
        self.record = GitRecord(store)

    @contextmanager
    def _state(self) -> Generator[sqlite3.Connection]:
        # Removing a sidecar removes its proposals through the foreign key.
        try:
            with connect(self.store.db_path, _TABLES) as connection:
                yield connection
        except OSError as exc:
            raise StoreError(f"Cannot open sidecar state at {self.store.db_path}: {exc}") from exc

    def submit(self, sidecar_id: str, path: str, version: str, content: str, summary: str, agent_name: str) -> dict[str, Any]:
        summary = summary.strip()
        if not summary or len(summary) > MAX_SUMMARY_LENGTH or not summary.isprintable():
            raise StoreError("Describe the proposed edit in one line of up to 500 characters.")
        if "\0" in content:
            raise StoreError("A proposed edit must be UTF-8 text.")
        content = content.replace("\r\n", "\n")
        with self.sources.locked(sidecar_id) as source:
            base = source.read(path)
            if base["version"] != version:
                raise FileConflict("The file changed. Read its current text before proposing an edit.", base["version"])
            result_version = hashlib.sha256(text_bytes(content, base["newline"], base["bom"], base["data"])).hexdigest()
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
                    (
                        proposal_id,
                        sidecar_id,
                        base["path"],
                        agent_name,
                        summary,
                        _now(),
                        version,
                        result_version,
                        base["newline"],
                        base["bom"],
                        base["content"],
                        content,
                    ),
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
                return {"items": [_summary(row) for row in rows], "pending_count": self._pending_count(state, sidecar_id)}
            rows = state.execute(
                f"""SELECT {_FIELDS}, sidecar_id, base_version FROM file_proposals
                    WHERE sidecar_id = ? AND status IN ('pending', 'applying')
                    ORDER BY created_at DESC, rowid DESC""",
                (sidecar_id,),
            ).fetchall()
        items = [{**_summary(row), "stale": self._current_version(row) != row["base_version"]} for row in rows]
        return {"items": items, "pending_count": len(rows)}

    @staticmethod
    def _find(state: sqlite3.Connection, sidecar_id: str, proposal_id: str) -> sqlite3.Row:
        row = state.execute(
            "SELECT * FROM file_proposals WHERE sidecar_id = ? AND id = ?",
            (sidecar_id, proposal_id),
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
            return self.sources.files(row["sidecar_id"]).read(row["path"])["version"]
        except (StoreError, OSError):
            return None

    def pending_counts(self) -> dict[str, int]:
        """Count proposals waiting for review in every sidecar that has any."""
        with self._state() as state:
            rows = state.execute(
                """SELECT sidecar_id, count(*) FROM file_proposals
                   WHERE status IN ('pending', 'applying') GROUP BY sidecar_id"""
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def find(self, reference: str) -> dict[str, str]:
        """Resolve a full proposal ID, or a unique prefix of one, to its sidecar and ID."""
        reference = reference.strip().lower()
        if len(reference) < MIN_ID_PREFIX or not _ID_PREFIX.fullmatch(reference):
            raise StoreError(f"Enter a proposal ID or at least its first {MIN_ID_PREFIX} characters.")
        with self._state() as state:
            rows = state.execute(
                "SELECT sidecar_id, id FROM file_proposals WHERE id LIKE ? LIMIT 2",
                (reference + "%",),
            ).fetchall()
        if not rows:
            raise StoreError(f'No proposal is identified by "{reference}".')
        if len(rows) > 1:
            raise StoreError(f'"{reference}" matches more than one proposal. Enter more of its ID.')
        return {"sidecar_id": rows[0]["sidecar_id"], "id": rows[0]["id"]}

    def _load(self, sidecar_id: str, proposal_id: str) -> tuple[sqlite3.Row, bool]:
        """Return the proposal, and whether an interrupted approval was just completed."""
        with self._state() as state:
            row = self._find(state, sidecar_id, proposal_id)
        if row["status"] != "applying":
            return row, False
        # An approval was recorded before the atomic file replacement. Recover
        # the completed write by hash, or leave it pending for another review.
        if self._current_version(row) == row["result_version"]:
            return self._finish(row, "approved"), True
        with self._state() as state:
            state.execute("UPDATE file_proposals SET status = 'pending' WHERE id = ?", (proposal_id,))
            return self._find(state, sidecar_id, proposal_id), False

    def _recover(self, sidecar_id: str, proposal_id: str) -> sqlite3.Row:
        with self.store.file_writes():
            row, recovered = self._load(sidecar_id, proposal_id)
        if recovered:
            self._after_approval(row)
        return row

    def detail(self, sidecar_id: str, proposal_id: str) -> dict[str, Any]:
        row = self._recover(sidecar_id, proposal_id)
        current = self._current_version(row) if row["status"] == "pending" else row["base_version"]
        stale = current != row["base_version"]
        before, proposed = row["before_text"], row["proposed_text"]
        return {
            **_summary(row),
            "stale": stale,
            "stale_message": str(_conflict(row, current)) if stale else None,
            # A decision clears the stored text, so only pending proposals have a diff.
            "diff": text_diff(before, proposed, row["path"]) if before is not None and proposed is not None else None,
        }

    def review(self, sidecar_id: str, proposal_id: str, decision: object) -> dict[str, Any]:
        """Record the owner's decision. An approval writes the file, then runs the steps that follow it."""
        if not isinstance(decision, str) or decision not in ("approved", "rejected"):
            raise StoreError("Choose whether to approve or reject the proposal.")
        self._recover(sidecar_id, proposal_id)
        approval: tuple[CommitPlan | None, bytes] | None = None
        with self.store.file_writes():
            row, _ = self._load(sidecar_id, proposal_id)
            if row["status"] not in ("pending", decision):
                raise StoreError(f"This proposal was already {row['status']}.")
            if row["status"] == "pending":
                if decision == "approved":
                    plan = self._plan_commit(row)
                    approval = (plan, self._write(row))
                row = self._finish(row, decision)
        result = _summary(row)
        if approval is not None:
            result.update(self._after_approval(row, *approval))
        return result

    def _plan_commit(self, row: sqlite3.Row) -> CommitPlan | None:
        """Check whether the approval can be recorded as a commit, before the file changes."""
        try:
            if not self.record.enabled(row["sidecar_id"]):
                return None
        except StoreError as exc:
            return CommitPlan(reason=f"{str(exc).rstrip('.')}.")
        try:
            files = self.sources.files(row["sidecar_id"])
            base = files.read_bytes(row["path"])
        except (StoreError, OSError) as exc:
            # The write that follows refuses the same unreadable file, so this reason is rarely shown.
            return CommitPlan(reason=f"Psydcar couldn't read {row['path']} to check it against git: {str(exc).rstrip('.')}.")
        return self.record.prepare(files.sidecar.source_path, row["path"], base)

    def _write(self, row: sqlite3.Row) -> bytes:
        """Write the approved text and return the file's new bytes."""
        current = self._current_version(row)
        if current != row["base_version"]:
            raise _conflict(row, current)
        with self._state() as state:
            state.execute("UPDATE file_proposals SET status = 'applying' WHERE id = ?", (row["id"],))
        try:
            written = self.sources.files(row["sidecar_id"]).write(
                row["path"],
                row["proposed_text"],
                row["base_version"],
                row["newline"],
                bool(row["bom"]),
            )
        except Exception as exc:
            # The atomic replacement did not happen, so the proposal waits for another review.
            with self._state() as state:
                state.execute("UPDATE file_proposals SET status = 'pending' WHERE id = ?", (row["id"],))
            if isinstance(exc, FileConflict):
                raise _conflict(row, exc.version) from exc
            if isinstance(exc, OSError):
                raise StoreError(
                    f"Couldn't write {row['path']}: {exc.strerror or exc}. The file was not changed, and the proposal is still pending."
                ) from exc
            raise
        return written["data"]

    def _after_approval(self, row: sqlite3.Row, plan: CommitPlan | None = None, approved: bytes = b"") -> dict[str, Any]:
        """Run the steps that follow an approved write. The approval stands whatever they report.

        The commit comes first, while the branch is most likely where the checks found it.
        """
        outcome: dict[str, Any] = {"git_record": plan is not None, "commit_id": None, "commit_branch": None, "commit_notice": None}
        if plan is not None:
            recorded = self.record.commit(
                plan,
                approved,
                agent=row["agent_name"],
                reason=row["summary"],
                proposal_id=row["id"],
            )
            outcome.update(recorded)
            commit_id = recorded.get("commit_id")
            if commit_id:
                try:
                    with self._state() as state:
                        state.execute("UPDATE file_proposals SET commit_id = ? WHERE id = ?", (commit_id, row["id"]))
                except (StoreError, sqlite3.Error):
                    pass  # The commit is in git's history whether or not the decision list shows it.
        return {**outcome, **self._update_index(row)}

    def _update_index(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            report = EvidenceStore(self.store).update(row["sidecar_id"])
        except UpdateSuperseded:
            # A later update started after the write, so it indexes the approved text.
            return {"index_notice": None}
        except StoreError as exc:
            reason = str(exc).rstrip(".")
            return {"index_notice": f"The search index wasn't updated: {reason}. Update the sidecar to make the change searchable."}
        if report.provider_error:
            return {"index_notice": f"Keyword search finds the change, but its embeddings weren't refreshed: {report.provider_error}"}
        return {"index_notice": None}
