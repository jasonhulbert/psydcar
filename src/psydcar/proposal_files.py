"""Read and write a sidecar's text source files for agent proposals.

Every path is relative to the sidecar folder and must stay inside the source scope:
no hidden or ignored folders, no symbolic links, and only indexed text extensions.
An agent reads a file to propose against it. An approved proposal is written atomically,
keeps the file's line endings and byte order mark, and is refused when the file changed after it was read.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .registry import Sidecar, SidecarStore, StoreError
from .source_scope import (
    MAX_FILE_BYTES, SUPPORTED_EXTENSIONS, is_hidden, is_ignored_directory, is_link, is_source_root, is_text_source,
    read_source,
)


MAX_PATH_LENGTH = 1024


class FileConflict(StoreError):
    """The file on disk no longer matches the version the proposal was made against."""

    def __init__(self, message: str, version: str | None):
        super().__init__(message)
        self.version = version


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_bytes(content: str, newline: str, bom: bool) -> bytes:
    if newline == "\r\n":
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    try:
        data = (b"\xef\xbb\xbf" if bom else b"") + content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise StoreError("Send valid UTF-8 text.") from exc
    if len(data) > MAX_FILE_BYTES:
        raise StoreError("The file would be larger than the 2 MiB text limit. Psydcar didn't save it.")
    return data


def _parts(relative_path: Any) -> list[str]:
    if not isinstance(relative_path, str) or not relative_path.strip("/"):
        raise StoreError("Enter a path inside the sidecar folder.")
    if len(relative_path) > MAX_PATH_LENGTH or "\0" in relative_path or "\\" in relative_path:
        raise StoreError("That path is too long or contains characters Psydcar doesn't allow.")
    parts = relative_path.strip("/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise StoreError("Paths can't contain empty, '.', or '..' segments.")
        if is_hidden(part):
            raise StoreError(f'"{part}" is hidden. Psydcar skips hidden files and folders.')
        if len(part.encode("utf-8")) > 255:
            raise StoreError(f'"{part[:40]}…" is longer than the file system allows.')
    for part in parts[:-1]:
        if is_ignored_directory(part):
            raise StoreError(f'Psydcar skips folders named "{part}".')
    return parts


def _require_text_name(name: str) -> None:
    if not is_text_source(name):
        allowed = " ".join(sorted(SUPPORTED_EXTENSIONS))
        raise StoreError(f"Use a text file extension Psydcar indexes: {allowed}")


class SourceFiles:
    """File operations scoped to one sidecar's source folder."""

    def __init__(self, sidecar: Sidecar):
        self.sidecar = sidecar
        root = sidecar.source_path
        if not is_source_root(root):
            raise StoreError(f"The sidecar folder is missing, a symbolic link, or not a folder: {root}")
        self.root = root.resolve()

    def _path(self, relative_path: Any) -> tuple[Path, str]:
        """Resolve a relative path, rejecting symbolic links anywhere along it."""
        parts = _parts(relative_path)
        current = self.root
        for part in parts:
            current = current / part
            if is_link(current):
                raise StoreError(f"{current.relative_to(self.root).as_posix()} is a symbolic link. Psydcar doesn't follow links.")
        if not current.resolve(strict=False).is_relative_to(self.root):
            raise StoreError("That path is outside the sidecar folder.")
        return current, "/".join(parts)

    def read(self, relative_path: Any) -> dict[str, Any]:
        path, clean = self._path(relative_path)
        _require_text_name(path.name)
        if not path.is_file():
            raise StoreError(f"{clean} doesn't exist or isn't a file.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise StoreError(f"{clean} is larger than the 2 MiB text limit, so agents can't propose edits to it.")
        data, _status, reason = read_source(path, self.root)
        if data is None:
            raise StoreError(f"{clean} can't be opened. {reason}")
        bom = data.startswith(b"\xef\xbb\xbf")
        text = data.decode("utf-8-sig")
        newline = "\r\n" if "\r\n" in text else "\n"
        return {
            "path": clean, "content": text.replace("\r\n", "\n"), "version": _version(data),
            "newline": newline, "bom": bom,
        }

    def read_bytes(self, relative_path: Any) -> bytes:
        """Return a text source file's bytes as they are on disk."""
        path, clean = self._path(relative_path)
        _require_text_name(path.name)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise StoreError(f"{clean} doesn't exist, isn't a file, or is larger than the 2 MiB text limit.")
        return path.read_bytes()

    def write(self, relative_path: Any, content: Any, version: Any, newline: Any = "\n", bom: Any = False) -> dict[str, Any]:
        if not isinstance(content, str) or not isinstance(version, str):
            raise StoreError("Send the file content and the version it was read at.")
        if newline not in ("\n", "\r\n") or not isinstance(bom, bool):
            raise StoreError("Line endings must be LF or CRLF.")
        path, clean = self._path(relative_path)
        _require_text_name(path.name)
        if not path.is_file():
            raise FileConflict(f"{clean} was moved or deleted after it was read.", None)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise FileConflict(f"{clean} grew beyond the 2 MiB text limit after it was read.", None)
        with path.open("rb") as stream:
            current = stream.read(MAX_FILE_BYTES + 1)
        if _version(current) != version:
            raise FileConflict(f"{clean} changed on disk after it was read.", _version(current))
        data = text_bytes(content, newline, bom)
        self._replace(path, data)
        return {"path": clean, "version": _version(data)}

    @staticmethod
    def _replace(path: Path, data: bytes) -> None:
        """Write through a temporary file in the same folder so a crash never leaves half a file."""
        mode = stat.S_IMODE(path.stat().st_mode)
        handle, temporary = tempfile.mkstemp(prefix=".psydcar-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


class Workspace:
    """Open a sidecar's source files, and serialize proposal writes across local processes."""

    def __init__(self, store: SidecarStore):
        self.store = store

    def files(self, sidecar_id: str) -> SourceFiles:
        return SourceFiles(self.store.get(sidecar_id, exact_id=True))

    @contextmanager
    def edit(self, sidecar_id: str) -> Iterator[SourceFiles]:
        with self.store.file_writes():
            yield self.files(sidecar_id)
