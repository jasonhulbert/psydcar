"""File filtering and scanning utilities for sidecar source roots."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from psydcar.filesystem import resolve_source_root

DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000
BINARY_SAMPLE_SIZE = 8192

ALLOWED_TEXT_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cfg",
        ".conf",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".htm",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".log",
        ".md",
        ".mdx",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)

IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".angular",
        ".cache",
        ".git",
        ".hg",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "env",
        "node_modules",
        "target",
        "venv",
    }
)


class SkipReason(StrEnum):
    NOT_A_FILE = "not_a_file"
    EXTENSION = "extension"
    OVERSIZED = "oversized"
    BINARY = "binary"


@dataclass(frozen=True)
class FileCandidate:
    path: Path
    relative_path: Path
    size_bytes: int
    extension: str


@dataclass(frozen=True)
class FileFilterResult:
    included: bool
    reason: SkipReason | None = None


def should_include_file(
    path: Path,
    *,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_TEXT_EXTENSIONS,
) -> FileFilterResult:
    """Return whether a file should be scanned for indexing."""

    if not path.is_file():
        return FileFilterResult(False, SkipReason.NOT_A_FILE)

    if path.suffix.lower() not in allowed_extensions:
        return FileFilterResult(False, SkipReason.EXTENSION)

    if path.stat().st_size > max_file_size_bytes:
        return FileFilterResult(False, SkipReason.OVERSIZED)

    if is_binary_file(path):
        return FileFilterResult(False, SkipReason.BINARY)

    return FileFilterResult(True)


def scan_files(
    source_root: Path | str,
    *,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    allowed_extensions: frozenset[str] = ALLOWED_TEXT_EXTENSIONS,
    ignored_directories: frozenset[str] = IGNORED_DIRECTORY_NAMES,
) -> Iterator[FileCandidate]:
    """Yield indexable file candidates beneath ``source_root``."""

    root = resolve_source_root(source_root)
    for directory_name, directory_names, file_names in os.walk(root):
        directory = Path(directory_name)
        directory_names[:] = [name for name in directory_names if name not in ignored_directories]
        for file_name in sorted(file_names):
            path = directory / file_name
            result = should_include_file(
                path,
                max_file_size_bytes=max_file_size_bytes,
                allowed_extensions=allowed_extensions,
            )
            if result.included:
                yield FileCandidate(
                    path=path,
                    relative_path=path.relative_to(root),
                    size_bytes=path.stat().st_size,
                    extension=path.suffix.lower(),
                )


def is_binary_file(path: Path) -> bool:
    """Detect likely binary files from an initial byte sample."""

    sample = path.read_bytes()[:BINARY_SAMPLE_SIZE]
    if not sample:
        return False
    if b"\0" in sample:
        return True

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
