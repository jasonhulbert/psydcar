"""Read browser assets from the folder containing a selected HTML entry point."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..files import IMAGE_TYPES, _parts
from ..store import StoreError


_BUILD_FOLDERS = frozenset({"build", "dist"})
_HTML = {".html", ".htm"}
_MAX_ASSET_BYTES = 20 * 1024 * 1024
_MEDIA_TYPES = {
    **IMAGE_TYPES,
    **dict.fromkeys(_HTML, "text/html"),
    ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".woff": "font/woff", ".woff2": "font/woff2",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".ogg": "audio/ogg", ".webm": "video/webm",
}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True)
class Artifact:
    folder: Path
    entry: str

    @classmethod
    def from_entry(cls, root: Path, path: str) -> Artifact:
        *folder, entry = _parts(path, allowed_ignored=_BUILD_FOLDERS)
        if Path(entry).suffix.lower() not in _HTML:
            raise StoreError("Choose an .html or .htm entry point.")
        artifact = cls(root.joinpath(*folder), entry)
        try:
            artifact.read(entry)
        except (OSError, StoreError) as exc:
            raise StoreError(f"Can't preview {path}. Choose a readable HTML file under 20 MiB.") from exc
        return artifact

    def read(self, path: str) -> tuple[bytes, str]:
        *folders, filename = _parts(path)
        media_type = _MEDIA_TYPES.get(Path(filename).suffix.lower())
        if media_type is None:
            raise StoreError("That file type isn't available in web previews.")

        # Open each component relative to an already opened directory. The
        # selected folder or an asset can change between requests.
        directory = os.open(self.folder.anchor, _DIRECTORY)
        try:
            for name in (*self.folder.parts[1:], *folders):
                child = os.open(name, _DIRECTORY, dir_fd=directory)
                os.close(directory)
                directory = child
            flags = os.O_NOFOLLOW | os.O_NONBLOCK
            with open(filename, "rb", opener=lambda name, mode: os.open(name, mode | flags, dir_fd=directory)) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_ASSET_BYTES:
                    raise StoreError("The asset isn't a regular file under 20 MiB.")
                data = stream.read(_MAX_ASSET_BYTES + 1)
                if len(data) > _MAX_ASSET_BYTES:
                    raise StoreError("The asset exceeds the 20 MiB preview limit.")
        finally:
            os.close(directory)
        return data, media_type
