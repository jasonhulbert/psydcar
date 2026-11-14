"""Read and apply synced files without following links in the source tree."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..evidence import MAX_FILE_BYTES, MAX_PDF_BYTES, SUPPORTED_EXTENSIONS, _walk_candidates
from ..files import IMAGE_TYPES, MAX_IMAGE_BYTES, FileConflict, SourceFiles
from ..store import StoreError
from ..sync_protocol import Blob, parts

_LIMITS = {**dict.fromkeys(SUPPORTED_EXTENSIONS, MAX_FILE_BYTES),
           **dict.fromkeys(IMAGE_TYPES, MAX_IMAGE_BYTES), ".pdf": MAX_PDF_BYTES}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def version(data: bytes) -> Blob:
    return {"digest": hashlib.sha256(data).hexdigest(), "size": len(data)}


def limit(path: str) -> int:
    try:
        parts(path)
        return _LIMITS[Path(path).suffix.lower()]
    except (ValueError, KeyError) as exc:
        raise StoreError(f"{path} is outside the supported sync file scope.") from exc


class SyncFiles:
    def __init__(self, files: SourceFiles):
        self.root = files.root

    def paths(self) -> Iterator[str]:
        def unavailable(error: OSError) -> None:
            raise StoreError(f"Cannot scan the sidecar for sync: {error.strerror}") from error

        for path, linked in _walk_candidates(self.root, unavailable, extensions=frozenset(_LIMITS)):
            if linked or path.is_symlink():
                continue
            yield path.relative_to(self.root).as_posix()

    @contextmanager
    def parent(self, path: str, *, create: bool = False) -> Iterator[int | None]:
        *folders, _ = parts(path)
        directory = os.open(self.root.anchor, _DIRECTORY)
        try:
            for name in self.root.parts[1:]:
                child = os.open(name, _DIRECTORY, dir_fd=directory)
                os.close(directory)
                directory = child
            for name in folders:
                if name not in os.listdir(directory):
                    if not create:
                        yield None
                        return
                    # mkdir refuses to shadow a differently spelled entry on case-insensitive disks.
                    os.mkdir(name, dir_fd=directory)
                try:
                    child = os.open(name, _DIRECTORY, dir_fd=directory)
                except NotADirectoryError:
                    if create:
                        raise
                    yield None
                    return
                os.close(directory)
                directory = child
            yield directory
        finally:
            os.close(directory)

    @staticmethod
    def _read(directory: int | None, name: str, maximum: int) -> tuple[bytes | None, int]:
        if directory is None or name not in os.listdir(directory):
            return None, 0
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(name, flags, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                return None, 0
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise StoreError(f"{name} is not a regular file within the sync size limit.")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(maximum + 1)
            latest = os.fstat(fd)
            if len(data) > maximum or info.st_mtime_ns != latest.st_mtime_ns or info.st_ctime_ns != latest.st_ctime_ns:
                raise FileConflict(f"{name} changed during sync. Try again.", None)
            return data, stat.S_IMODE(info.st_mode)
        finally:
            os.close(fd)

    def read(self, path: str) -> bytes | None:
        maximum = limit(path)
        try:
            with self.parent(path) as directory:
                return self._read(directory, path.rsplit("/", 1)[-1], maximum)[0]
        except OSError as exc:
            raise StoreError(f"Cannot read {path} for sync: {exc.strerror}") from exc

    def apply(self, path: str, expected: Blob | None, data: bytes | None) -> None:
        maximum = limit(path)
        if data is not None and len(data) > maximum:
            raise StoreError(f"{path} exceeds the sync size limit.")
        name = path.rsplit("/", 1)[-1]
        try:
            with self.parent(path, create=data is not None and expected is None) as directory:
                before, mode = self._read(directory, name, maximum)
                current = version(before) if before is not None else None
                if current != expected:
                    raise FileConflict(f"{path} changed on disk. Review the current version.", (current or {}).get("digest"))
                if directory is None or before is None and data is None:
                    return
                if data is None:
                    os.unlink(name, dir_fd=directory)
                    return
                temporary = f".psydcar-sync-{uuid.uuid4().hex}.tmp"
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode or 0o644, dir_fd=directory)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        if before is not None:
                            os.fchmod(stream.fileno(), mode)
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Check again after writing potentially large temporary files.
                    latest, _ = self._read(directory, name, maximum)
                    if latest != before:
                        raise FileConflict(f"{path} changed on disk. Review the current version.", None)
                    if before is None:
                        if name in os.listdir(directory) and stat.S_ISDIR(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
                            os.rmdir(name, dir_fd=directory)
                        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    else:
                        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            raise StoreError(f"Cannot apply sync to {path}: {exc.strerror}. Check for a folder or link at that path.") from exc
