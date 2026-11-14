"""The source scope: which files in a sidecar folder count as source.

Indexing, proposal file reads, and the watcher all ask this module. A source file has a supported extension,
is not hidden, is not a symbolic link, does not sit in a hidden or ignored folder, and stays within its size limit.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

from .registry import Sidecar

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".sh",
        ".sql",
        ".html",
        ".css",
    }
)
PDF_EXTENSION = ".pdf"
IGNORED_DIRECTORIES = frozenset({"node_modules", "__pycache__", "dist", "build", "target", "vendor"})
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024


def is_link(path: Path) -> bool:
    """Psydcar never follows symbolic links, so a linked file or folder is outside the scope."""
    return path.is_symlink()


def is_source_root(root: Path) -> bool:
    return not is_link(root) and root.is_dir()


def is_hidden(name: str) -> bool:
    return name.startswith(".")


def is_ignored_directory(name: str) -> bool:
    return name in IGNORED_DIRECTORIES


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == PDF_EXTENSION


def is_text_source(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def size_limit(path: Path) -> tuple[int, str]:
    """Return the largest size a source may have and how to name that limit."""
    return (MAX_PDF_BYTES, "50 MiB PDF") if is_pdf(path) else (MAX_FILE_BYTES, "2 MiB text")


def walk(root: Path, onerror: Callable[[OSError], None]) -> Iterator[tuple[Path, bool]]:
    """Yield files in the source scope and linked directories the walk must not enter."""
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        traversable = []
        for name in dirs:
            if is_hidden(name) or is_ignored_directory(name):
                continue
            child = Path(directory) / name
            if is_link(child):
                yield child, True
            else:
                traversable.append(name)
        dirs[:] = sorted(traversable)
        for name in sorted(files):
            if is_hidden(name) or not (is_text_source(name) or is_pdf(Path(name))):
                continue
            yield Path(directory) / name, False


def read_source(path: Path, root: Path | None = None) -> tuple[bytes | None, str, str | None]:
    """Read a regular UTF-8 file once; return bytes, status, and a clear failure reason."""
    data, status, reason = read_bytes(path, root)
    if data is None:
        return None, status, reason
    if b"\0" in data:
        return None, "unreadable", "Source contains binary data."
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "unreadable", "Source is not UTF-8 text."
    return data, "current", None


def read_bytes(path: Path, root: Path | None = None) -> tuple[bytes | None, str, str | None]:
    """Read current bytes while enforcing the source-tree boundary."""
    try:
        if is_link(path):
            return None, "unreadable", "Source is a symbolic link."
        if root is not None and not path.resolve(strict=False).is_relative_to(root):
            return None, "unreadable", "Source path no longer stays inside the sidecar tree."
        data = path.read_bytes()
    except FileNotFoundError:
        return None, "missing", "Source file no longer exists."
    except OSError as exc:
        return None, "unreadable", f"Source file cannot be read: {exc.strerror or exc}"
    except RuntimeError as exc:
        return None, "unreadable", f"Source path cannot be resolved: {exc}"
    return data, "current", None


def source_snapshot(sidecar: Sidecar) -> tuple[tuple[str, str, int, int, int, int], ...]:
    """Cheap watcher fingerprint with the updater's exact source scope."""
    root = sidecar.source_path
    if is_link(root):
        return ((".", "linked_root", 0, 0, 0, 0),)
    if not root.is_dir():
        return ((".", "missing_root", 0, 0, 0, 0),)
    entries: list[tuple[str, str, int, int, int, int]] = []

    def walk_error(error: OSError) -> None:
        entries.append((str(error.filename or root), "walk_error", error.errno or 0, 0, 0, 0))

    for path, linked_directory in walk(root, walk_error):
        relative = path.relative_to(root).as_posix()
        try:
            stat = path.lstat()
        except OSError as exc:
            entries.append((relative, "stat_error", exc.errno or 0, 0, 0, 0))
            continue
        entries.append(
            (
                relative,
                "linked_directory" if linked_directory else "file",
                stat.st_mode,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
        )
    return tuple(sorted(entries))
