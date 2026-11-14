"""Read-only git state for sidecar folders that sit in a git repository.

Psydcar calls the git executable here and never changes the repository. It
takes no optional locks, so the owner's own git commands are never blocked.
When git is missing, the folder is not in a repository, or git fails, callers
get nothing back and behave as if git did not exist.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT_SECONDS = 10

# Repository settings that could start a daemon or a hook during a read.
_READ_ONLY_CONFIG = ("-c", "core.fsmonitor=false")


@dataclass(frozen=True)
class Repository:
    """The work tree that contains a folder, and the folder's path inside it."""

    root: Path
    prefix: str

    def path_of(self, relative_path: str) -> str:
        """Return a folder-relative path as a path from the repository root."""
        return self.prefix + relative_path


# Variables that point git at a particular repository, from `git rev-parse --local-env-vars`.
# They are dropped so that git finds the repository from the folder, as it would for the owner.
_REPOSITORY_VARIABLES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_OBJECT_DIRECTORY",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_PREFIX",
        "GIT_SHALLOW_FILE",
        "GIT_COMMON_DIR",
    }
)


def git_environment() -> dict[str, str]:
    """Return an environment in which git finds the repository from its working folder.

    Variables that point git at another repository or index are dropped, and git
    never prompts, takes optional locks, or reads pathspecs as patterns.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _REPOSITORY_VARIABLES and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LITERAL_PATHSPECS": "1",
            "LC_ALL": "C",
        }
    )
    return environment


def _git(cwd: Path, *args: str) -> str | None:
    environment = git_environment()
    try:
        completed = subprocess.run(
            ["git", *_READ_ONLY_CONFIG, *args],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", "surrogateescape")


def find_repository(folder: Path) -> Repository | None:
    """Return the repository whose work tree contains the folder, or None."""
    output = _git(folder, "rev-parse", "--show-toplevel", "--show-prefix")
    if output is None:
        return None
    lines = output.split("\n")
    if len(lines) < 2 or not lines[0]:
        return None
    return Repository(Path(lines[0]), lines[1])


def observe(files: Iterable[tuple[Path, str]]) -> dict[tuple[Path, str], dict[str, object]]:
    """Report the current commit and uncommitted state of files given as (folder, relative path).

    Each repository is read with one status call and one index listing, no
    matter how many files it holds. Files outside a repository are left out.
    """
    by_folder: dict[Path, set[str]] = {}
    for folder, relative_path in files:
        by_folder.setdefault(folder, set()).add(relative_path)
    by_root: dict[Path, list[tuple[Path, str, str]]] = {}
    for folder, relative_paths in by_folder.items():
        repository = find_repository(folder)
        if repository is None:
            continue
        for relative_path in relative_paths:
            by_root.setdefault(repository.root, []).append((folder, relative_path, repository.path_of(relative_path)))
    observed: dict[tuple[Path, str], dict[str, object]] = {}
    for root, entries in by_root.items():
        pathspecs = sorted({repository_path for _, _, repository_path in entries})
        state = _read_state(root, pathspecs)
        if state is None:
            continue
        commit, tracked, changed = state
        for folder, relative_path, repository_path in entries:
            is_tracked = repository_path in tracked
            observed[(folder, relative_path)] = {
                "commit": commit,
                "uncommitted": not is_tracked or repository_path in changed,
                "tracked": is_tracked,
            }
    return observed


def _read_state(root: Path, pathspecs: list[str]) -> tuple[str | None, set[str], set[str]] | None:
    status = _git(
        root,
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--untracked-files=no",
        "--no-renames",
        "--ignore-submodules=all",
        "--",
        *pathspecs,
    )
    listing = _git(root, "ls-files", "-z", "--full-name", "--", *pathspecs)
    if status is None or listing is None:
        return None
    commit: str | None = None
    changed: set[str] = set()
    for record in status.split("\0"):
        if record.startswith("# branch.oid "):
            oid = record.removeprefix("# branch.oid ")
            commit = None if oid == "(initial)" else oid
        elif record.startswith("1 "):
            changed.add(record.split(" ", 8)[8])
        elif record.startswith("u "):
            changed.add(record.split(" ", 10)[10])
    tracked = {path for path in listing.split("\0") if path}
    return commit, tracked, changed
