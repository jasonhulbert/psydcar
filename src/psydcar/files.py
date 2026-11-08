"""Browse and change a sidecar's text source files from the dashboard.

Every path is relative to the sidecar folder and must stay inside the indexer's scope:
no hidden or ignored folders, no symbolic links, and only indexed text extensions.
Saves are atomic and refuse to overwrite a file that changed after it was opened.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence import IGNORED_DIRECTORIES, MAX_FILE_BYTES, SUPPORTED_EXTENSIONS, _read_source
from .store import Sidecar, StoreError


MAX_TREE_ENTRIES = 5000
MAX_PATH_LENGTH = 1024
_MAC_JUNK = frozenset({".DS_Store"})


class FileConflict(StoreError):
    """The file on disk no longer matches the version the editor started from."""

    def __init__(self, message: str, version: str | None):
        super().__init__(message)
        self.version = version


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec="seconds")}


def _parts(relative_path: Any) -> list[str]:
    if not isinstance(relative_path, str) or not relative_path.strip("/"):
        raise StoreError("Enter a path inside the sidecar folder.")
    if len(relative_path) > MAX_PATH_LENGTH or "\0" in relative_path or "\\" in relative_path:
        raise StoreError("That path is too long or contains characters Psydcar doesn't allow.")
    parts = relative_path.strip("/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise StoreError("Paths can't contain empty, '.', or '..' segments.")
        if part.startswith("."):
            raise StoreError(f'"{part}" is hidden. Psydcar skips hidden files and folders.')
        if len(part.encode("utf-8")) > 255:
            raise StoreError(f'"{part[:40]}…" is longer than the file system allows.')
    for part in parts[:-1]:
        if part in IGNORED_DIRECTORIES:
            raise StoreError(f'Psydcar skips folders named "{part}".')
    return parts


def _require_text_name(name: str) -> None:
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = " ".join(sorted(SUPPORTED_EXTENSIONS))
        raise StoreError(f"Use a text file extension Psydcar indexes: {allowed}")


class SourceFiles:
    """File operations scoped to one sidecar's source folder."""

    def __init__(self, sidecar: Sidecar):
        self.sidecar = sidecar
        root = sidecar.source_path
        if root.is_symlink() or not root.is_dir():
            raise StoreError(f"The sidecar folder is missing, a symbolic link, or not a folder: {root}")
        self.root = root.resolve()

    def _path(self, relative_path: Any) -> tuple[Path, str]:
        """Resolve a relative path, rejecting symbolic links anywhere along it."""
        parts = _parts(relative_path)
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StoreError(f"{current.relative_to(self.root).as_posix()} is a symbolic link. Psydcar doesn't follow links.")
        if not current.resolve(strict=False).is_relative_to(self.root):
            raise StoreError("That path is outside the sidecar folder.")
        return current, "/".join(parts)

    def tree(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        truncated = False
        for directory, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
            base = Path(directory)
            dirs[:] = sorted(
                name for name in dirs
                if not name.startswith(".") and name not in IGNORED_DIRECTORIES and not (base / name).is_symlink()
            )
            for name in dirs:
                entries.append({"path": (base / name).relative_to(self.root).as_posix(), "type": "folder"})
            for name in sorted(files):
                path = base / name
                if name.startswith(".") or path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.is_symlink():
                    continue
                try:
                    info = path.stat()
                except OSError:
                    continue
                entries.append({"path": path.relative_to(self.root).as_posix(), "type": "file", "size": info.st_size,
                                "mtime_ns": info.st_mtime_ns, "editable": info.st_size <= MAX_FILE_BYTES})
            if len(entries) >= MAX_TREE_ENTRIES:
                truncated = True
                break
        entries = sorted(entries[:MAX_TREE_ENTRIES], key=lambda entry: entry["path"].split("/"))
        return {"root": str(self.sidecar.source_path), "entries": entries, "truncated": truncated}

    def read(self, relative_path: Any) -> dict[str, Any]:
        path, clean = self._path(relative_path)
        _require_text_name(path.name)
        if not path.is_file():
            raise StoreError(f"{clean} doesn't exist or isn't a file.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise StoreError(f"{clean} is larger than the 2 MiB text limit, so it can't be edited here.")
        data, _status, reason = _read_source(path, self.root)
        if data is None:
            raise StoreError(f"{clean} can't be opened. {reason}")
        bom = data.startswith(b"\xef\xbb\xbf")
        text = data.decode("utf-8-sig")
        newline = "\r\n" if "\r\n" in text else "\n"
        return {
            "path": clean, "content": text.replace("\r\n", "\n"), "version": _version(data),
            "newline": newline, "bom": bom, **_stamp(path),
        }

    def write(self, relative_path: Any, content: Any, version: Any, newline: Any = "\n", bom: Any = False) -> dict[str, Any]:
        if not isinstance(content, str) or not isinstance(version, str):
            raise StoreError("Send the file content and the version it was opened at.")
        if newline not in ("\n", "\r\n") or not isinstance(bom, bool):
            raise StoreError("Line endings must be LF or CRLF.")
        path, clean = self._path(relative_path)
        _require_text_name(path.name)
        if not path.is_file():
            raise FileConflict(f"{clean} was moved or deleted after you opened it.", None)
        current = path.read_bytes()
        if _version(current) != version:
            raise FileConflict(f"{clean} changed on disk after you opened it.", _version(current))
        if newline == "\r\n":
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        data = (b"\xef\xbb\xbf" if bom else b"") + content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise StoreError("The file would be larger than the 2 MiB text limit. Psydcar didn't save it.")
        self._replace(path, data)
        return {"path": clean, "version": _version(data), **_stamp(path)}

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

    def _make_parents(self, path: Path) -> None:
        missing = []
        for parent in path.parents:
            if parent == self.root or parent.exists():
                break
            missing.append(parent)
        for parent in reversed(missing):
            parent.mkdir()

    def create(self, relative_path: Any, kind: Any) -> dict[str, Any]:
        if kind not in ("file", "folder"):
            raise StoreError("Choose whether to create a file or a folder.")
        path, clean = self._path(relative_path)
        if kind == "file":
            _require_text_name(path.name)
        elif path.name in IGNORED_DIRECTORIES:
            raise StoreError(f'Psydcar skips folders named "{path.name}", so it wouldn\'t appear here.')
        if os.path.lexists(path):
            raise StoreError(f"{clean} already exists.")
        self._make_parents(path)
        try:
            if kind == "folder":
                path.mkdir()
            else:
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        except FileExistsError as exc:
            raise StoreError(f"{clean} already exists.") from exc
        return {"path": clean, "type": kind}

    def move(self, source: Any, target: Any) -> dict[str, Any]:
        source_path, source_clean = self._path(source)
        target_path, target_clean = self._path(target)
        if not source_path.exists():
            raise StoreError(f"{source_clean} doesn't exist.")
        is_folder = source_path.is_dir()
        if is_folder:
            if target_path.name in IGNORED_DIRECTORIES:
                raise StoreError(f'Psydcar skips folders named "{target_path.name}".')
            if target_path.is_relative_to(source_path):
                raise StoreError("A folder can't be moved inside itself.")
        else:
            _require_text_name(target_path.name)
        # A case-only rename on a case-insensitive disk points at the same file, which is fine.
        if os.path.lexists(target_path) and not os.path.samefile(source_path, target_path):
            raise StoreError(f"{target_clean} already exists.")
        self._make_parents(target_path)
        try:
            os.rename(source_path, target_path)
        except OSError as exc:
            raise StoreError(f"Couldn't move {source_clean}: {exc.strerror or exc}") from exc
        return {"from": source_clean, "path": target_clean, "type": "folder" if is_folder else "file"}

    def delete(self, relative_path: Any) -> dict[str, Any]:
        path, clean = self._path(relative_path)
        if path.is_dir():
            leftovers = [item for item in path.iterdir() if item.name not in _MAC_JUNK]
            if leftovers:
                raise StoreError(f"{clean} isn't empty. Delete or move its contents first.")
            for item in path.iterdir():
                item.unlink()
            path.rmdir()
            return {"path": clean, "type": "folder"}
        if not path.is_file():
            raise StoreError(f"{clean} doesn't exist.")
        _require_text_name(path.name)
        path.unlink()
        return {"path": clean, "type": "file"}
