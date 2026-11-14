"""Wire format shared by the sync service and the local app."""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from typing import TypedDict

MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 5000
MAX_MANIFEST = 1024 * 1024
MAX_SIDECAR_BYTES = 1024 * 1024 * 1024
ID = re.compile(r"[a-f0-9]{32}")
SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
DIGEST = re.compile(r"[a-f0-9]{64}")
IGNORED = {"node_modules", "__pycache__", "dist", "build", "target", "vendor"}


class Blob(TypedDict):
    digest: str
    size: int


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def checksum(digest: str) -> str:
    return base64.b64encode(bytes.fromhex(digest)).decode("ascii")


def parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path or len(path) > 1024 or "\\" in path:
        raise ValueError("Invalid sync path.")
    names = path.split("/")
    for name in names:
        if not name or name.startswith(".") or len(name.encode()) > 255 or not name.isprintable():
            raise ValueError("Invalid sync path segment.")
    if IGNORED.intersection(names[:-1]):
        raise ValueError("Sync skips generated folders.")
    return names


def blob(value: object) -> Blob:
    if not isinstance(value, dict):
        raise ValueError("Invalid file version.")
    digest, size = value.get("digest"), value.get("size")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest) or type(size) is not int or not 0 <= size <= MAX_BYTES:
        raise ValueError("Invalid file version.")
    return {"digest": digest, "size": size}


def manifest(value: object) -> dict[str, Blob]:
    if not isinstance(value, dict) or len(value) > MAX_FILES:
        raise ValueError("A synced sidecar can have up to 5,000 files.")
    files: dict[str, Blob] = {}
    # Check all directory prefixes as well as files for portable spelling.
    names: dict[str, str] = {}
    folders: set[str] = set()
    sizes: dict[str, int] = {}
    for path, version in value.items():
        segments = parts(path)
        for index in range(1, len(segments) + 1):
            prefix = "/".join(segments[:index])
            key = unicodedata.normalize("NFC", prefix).casefold()
            if names.setdefault(key, prefix) != prefix:
                raise ValueError("Sync paths must have distinct names regardless of case or Unicode spelling.")
            if index < len(segments):
                folders.add(prefix)
        item = blob(version)
        if sizes.setdefault(item["digest"], item["size"]) != item["size"]:
            raise ValueError("The same file version has conflicting sizes.")
        files[path] = item
    if folders.intersection(files):
        raise ValueError("A sync path cannot be both a file and a folder.")
    if sum(sizes.values()) > MAX_SIDECAR_BYTES:
        raise ValueError("This sidecar exceeds the 1 GiB sync limit.")
    if len(encode(files)) > MAX_MANIFEST:
        raise ValueError("This sidecar has too many long paths to sync.")
    return files
