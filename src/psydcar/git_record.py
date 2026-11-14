"""Record approved proposals as commits in the repository that holds a sidecar's folder.

The record is off until the owner turns it on for a sidecar, and it is offered
only where the folder is in a git repository. With the record on, each approval
becomes one commit on the current branch. The commit holds exactly the approved
change to one file. It is built from the branch tip in a private index, so the
owner's staged work and other uncommitted changes never enter it. When that
cannot be guaranteed, no commit is made and the reason is reported.

The approval never depends on git. The file is written first, and nothing here
raises. Git hooks do not run for these commits. Psydcar never pulls, pushes,
fetches, or touches remotes.
"""

from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git_state import find_repository, git_environment
from .registry import Sidecar, SidecarStore, StoreError
from .storage import Tables, connect

GIT_TIMEOUT_SECONDS = 10
# Signing can wait on a key agent, so the commit itself gets longer.
COMMIT_TIMEOUT_SECONDS = 60

_TABLES = Tables(
    "git_record",
    "git_records",
    (
        (
            """CREATE TABLE git_records (
        sidecar_id TEXT PRIMARY KEY REFERENCES sidecars(id) ON DELETE CASCADE,
        enabled_at TEXT NOT NULL
    )""",
        ),
    ),
)

# No hook runs, and no file system monitor starts, for a commit Psydcar makes.
_CONFIG = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")

# Work that leaves the repository busy or between commits, and how to name it.
_IN_PROGRESS = (
    ("index.lock", "Another git command"),
    ("MERGE_HEAD", "A merge"),
    ("rebase-merge", "A rebase"),
    ("rebase-apply", "A rebase or patch series"),
    ("CHERRY_PICK_HEAD", "A cherry-pick"),
    ("REVERT_HEAD", "A revert"),
    ("BISECT_LOG", "A bisect"),
)


class _GitError(Exception):
    """A git command failed. The message is ready to show to the owner."""


@dataclass(frozen=True)
class CommitPlan:
    """What one approval may commit, checked before the file is written.

    ``reason`` is set when no commit can be limited to the approved change.
    """

    reason: str | None = None
    root: Path | None = None
    path: str = ""
    head: str = ""
    blob: str = ""
    mode: str = ""
    branch: str = ""


def _run(
    root: Path, *args: str, data: bytes | None = None, index: Path | None = None, timeout: float = GIT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[bytes]:
    environment = git_environment()
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    try:
        return subprocess.run(
            ["git", *_CONFIG, *args],
            cwd=root,
            env=environment,
            input=data if data is not None else b"",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitError(f"Git didn't finish `git {args[0]}` within {int(timeout)} seconds.") from exc
    except OSError as exc:
        raise _GitError(f"Git couldn't be started: {exc.strerror or exc}.") from exc


def _detail(completed: subprocess.CompletedProcess[bytes]) -> str:
    lines = completed.stderr.decode("utf-8", "replace").strip().splitlines()
    detail = next((line.strip() for line in lines if line.strip()), f"exit status {completed.returncode}")
    return detail.removeprefix("fatal: ").removeprefix("error: ").rstrip(".")


def _git(root: Path, *args: str, data: bytes | None = None, index: Path | None = None, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    completed = _run(root, *args, data=data, index=index, timeout=timeout)
    if completed.returncode != 0:
        raise _GitError(f"Git reported an error: {_detail(completed)}.")
    return completed.stdout.decode("utf-8", "surrogateescape")


def _index_entry(root: Path, path: str) -> tuple[str, str] | None:
    """Return the (mode, object) the owner's index holds for a path, or None if it holds anything else."""
    entries = [entry for entry in _git(root, "ls-files", "-s", "-z", "--", path).split("\0") if entry]
    if len(entries) != 1:
        return None
    info, _, name = entries[0].partition("\t")
    mode, oid, stage = info.split(" ")
    return (mode, oid) if name == path and stage == "0" else None


def _message(agent: str, reason: str, proposal_id: str) -> bytes:
    return (
        f"{reason}\n\nProposed by {agent} and approved by the owner in Psydcar.\n\nPsydcar-Agent: {agent}\nPsydcar-Proposal: {proposal_id}\n"
    ).encode()


def _not_recorded(reason: str) -> dict[str, Any]:
    return {"commit_id": None, "commit_branch": None, "commit_notice": f"No commit was recorded. {reason} The approved edit was saved."}


class GitRecord:
    """The per-sidecar setting, and the commit that follows an approval while it is on."""

    def __init__(self, store: SidecarStore):
        self.store = store

    @contextmanager
    def _state(self) -> Generator[sqlite3.Connection]:
        # Removing a sidecar removes its setting through the foreign key.
        try:
            with connect(self.store.db_path, _TABLES) as connection:
                yield connection
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"Cannot read the git record setting at {self.store.db_path}: {exc}") from exc

    def enabled(self, sidecar_id: str) -> bool:
        with self._state() as state:
            return state.execute("SELECT 1 FROM git_records WHERE sidecar_id = ?", (sidecar_id,)).fetchone() is not None

    def state(self, sidecar: Sidecar) -> dict[str, Any]:
        """Report whether the record is on, and where its commits would go."""
        repository = find_repository(sidecar.source_path)
        branch = None
        if repository is not None:
            try:
                branch = _git(repository.root, "symbolic-ref", "-q", "--short", "HEAD").strip() or None
            except _GitError:
                branch = None
        return {
            "available": repository is not None,
            "enabled": self.enabled(sidecar.id),
            "repository": str(repository.root) if repository is not None else None,
            "branch": branch,
        }

    def set(self, reference: str, enabled: bool) -> tuple[Sidecar, dict[str, Any]]:
        """Turn the record on or off. It can be turned on only for a folder in a git repository."""
        sidecar = self.store.get(reference)
        if enabled and find_repository(sidecar.source_path) is None:
            raise StoreError(f"The folder of \"{sidecar.name}\" isn't in a git repository, so approvals can't be recorded as commits.")
        with self._state() as state:
            if enabled:
                state.execute(
                    "INSERT OR IGNORE INTO git_records (sidecar_id, enabled_at) VALUES (?, ?)",
                    (sidecar.id, datetime.now(UTC).isoformat(timespec="seconds")),
                )
            else:
                state.execute("DELETE FROM git_records WHERE sidecar_id = ?", (sidecar.id,))
        return sidecar, self.state(sidecar)

    def prepare(self, folder: Path, relative_path: str, base: bytes) -> CommitPlan:
        """Check, before an approval writes the file, that a commit can hold only the approved change.

        ``base`` is the file's content as the agent read it. The commit is possible
        when the branch tip, the owner's index, and the file on disk all agree on it.
        """
        try:
            return self._prepare(folder, relative_path, base)
        except _GitError as exc:
            return CommitPlan(reason=str(exc))
        except Exception as exc:  # The approval never depends on git.
            return CommitPlan(reason=f"Psydcar couldn't check the repository ({type(exc).__name__}).")

    def _prepare(self, folder: Path, relative_path: str, base: bytes) -> CommitPlan:
        repository = find_repository(folder)
        if repository is None:
            return CommitPlan(reason="The folder is no longer in a git repository, or git isn't available.")
        root, path = repository.root, repository.path_of(relative_path)
        names = [name for name, _ in _IN_PROGRESS]
        locations = _git(root, "rev-parse", *(part for name in names for part in ("--git-path", name))).splitlines()
        for (_, operation), location in zip(_IN_PROGRESS, locations, strict=False):
            if (root / location).exists():
                return CommitPlan(reason=f"{operation} is {'running' if location.endswith('.lock') else 'in progress'} in the repository.")
        branch = _run(root, "symbolic-ref", "-q", "HEAD")
        if branch.returncode != 0:
            return CommitPlan(reason="HEAD is detached, so there is no branch to commit to.")
        head = _run(root, "rev-parse", "-q", "--verify", "HEAD^{commit}")
        if head.returncode != 0:
            return CommitPlan(reason="The repository has no commits yet.")
        tree = _git(root, "ls-tree", "-z", "HEAD", "--", path).split("\0")[0]
        info, _, name = tree.partition("\t")
        if name != path or info.split(" ")[1:2] != ["blob"]:
            ignored = bool(_git(root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", path))
            return CommitPlan(reason=f"{relative_path} {'is ignored' if ignored else 'is not tracked'} by git.")
        mode, _, blob = info.split(" ")
        if _index_entry(root, path) != (mode, blob):
            return CommitPlan(reason=f"{relative_path} had staged changes, and a commit would have included them.")
        if _git(root, "hash-object", f"--path={path}", "--stdin", data=base).strip() != blob:
            return CommitPlan(reason=f"{relative_path} already had uncommitted changes, and a commit would have included them.")
        return CommitPlan(
            root=root,
            path=path,
            head=head.stdout.decode().strip(),
            blob=blob,
            mode=mode,
            branch=branch.stdout.decode("utf-8", "surrogateescape").strip().removeprefix("refs/heads/"),
        )

    def commit(self, plan: CommitPlan, approved: bytes, *, agent: str, reason: str, proposal_id: str) -> dict[str, Any]:
        """Commit the approved content on the planned branch. Never raises.

        Returns the new commit's ID and branch, or None, and a notice for the
        owner when something did not go as planned.
        """
        if plan.reason is not None or plan.root is None:
            return _not_recorded(plan.reason or "The repository could not be checked.")
        try:
            return self._commit(plan, plan.root, approved, _message(agent, reason, proposal_id))
        except _GitError as exc:
            return _not_recorded(str(exc))
        except Exception as exc:  # The approval never depends on git.
            return _not_recorded(f"Psydcar couldn't make the commit ({type(exc).__name__}).")

    def _commit(self, plan: CommitPlan, root: Path, approved: bytes, message: bytes) -> dict[str, Any]:
        blob = _git(root, "hash-object", "-w", f"--path={plan.path}", "--stdin", data=approved).strip()
        # Build the tree in a private index from the branch tip, so nothing the
        # owner staged or changed elsewhere can enter the commit.
        with tempfile.TemporaryDirectory(prefix="psydcar-commit-") as scratch:
            index = Path(scratch) / "index"
            _git(root, "read-tree", plan.head, index=index)
            _git(root, "update-index", "--cacheinfo", plan.mode, blob, plan.path, index=index)
            tree = _git(root, "write-tree", index=index).strip()
        if tree == _git(root, "rev-parse", f"{plan.head}^{{tree}}").strip():
            return _not_recorded(f"Git stores the approved {plan.path} the same as the last commit.")
        # The plumbing command doesn't read commit.gpgSign, so the owner's choice is passed on.
        signing = _run(root, "config", "--type=bool", "--get", "commit.gpgSign").stdout.strip() == b"true"
        made = _run(
            root, "commit-tree", tree, "-p", plan.head, *(("-S",) if signing else ()), "-F", "-", data=message, timeout=COMMIT_TIMEOUT_SECONDS
        )
        if made.returncode != 0:
            errors = made.stderr.decode("utf-8", "replace").lower()
            if "failed to sign" in errors or "gpg" in errors or "ssh-keygen" in errors:
                raise _GitError("Git couldn't sign the commit. Check your commit signing setup.")
            if "tell me who you are" in errors or "ident" in errors or "auto-detect" in errors:
                raise _GitError("Git has no user name and email to commit with. Set user.name and user.email with git config.")
            raise _GitError(f"Git reported an error: {_detail(made)}.")
        commit = made.stdout.decode().strip()
        # Move the branch only if it still points where the checks found it.
        moved = _run(root, "update-ref", "-m", "psydcar: record an approved proposal", "HEAD", commit, plan.head)
        if moved.returncode != 0:
            raise _GitError("The branch moved while the commit was being made.")
        # Keep the owner's index in step for this one file, so it does not show the
        # earlier text as staged. Leave it alone if the owner staged something since.
        notice = None
        try:
            if _index_entry(root, plan.path) == (plan.mode, plan.blob):
                _git(root, "update-index", "--cacheinfo", plan.mode, blob, plan.path)
            else:
                notice = f"Changes to {plan.path} that you staged meanwhile were left staged."
        except _GitError:
            notice = f"Git's staging area still lists the earlier {plan.path}. Run `git reset -q -- {plan.path}` in {root} to match the commit."
        return {"commit_id": commit, "commit_branch": plan.branch, "commit_notice": notice}
