"""Reject a release tag or distribution that does not match the package."""

from __future__ import annotations

import configparser
import email
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path


REQUIRED_PACKAGE_FILES = {
    "psydcar/__init__.py",
    "psydcar/cli.py",
    "psydcar/dashboard.py",
    "psydcar/mcp_server.py",
    "psydcar/store.py",
    "psydcar/dashboard_assets/index.html",
    "psydcar/dashboard_assets/dashboard.css",
    "psydcar/dashboard_assets/dashboard.js",
}


def check(tag: str, dist: Path) -> None:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError(f"Expected a vMAJOR.MINOR.PATCH tag, got {tag!r}")
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    if tag != f"v{version}":
        raise ValueError(f"Tag {tag} does not match pyproject.toml version {version}")

    wheel = dist / f"psydcar-{version}-py3-none-any.whl"
    sdist = dist / f"psydcar-{version}.tar.gz"
    if set(dist.iterdir()) != {wheel, sdist}:
        raise ValueError(f"Expected only {wheel.name} and {sdist.name} in {dist}")

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        if any(".env" in Path(name).parts for name in members):
            raise ValueError("Wheel contains a local .env file")
        missing = REQUIRED_PACKAGE_FILES - members
        if missing:
            raise ValueError(f"Wheel is missing: {sorted(missing)}")
        metadata = email.message_from_bytes(archive.read(f"psydcar-{version}.dist-info/METADATA"))
        if metadata["Name"] != "psydcar" or metadata["Version"] != version:
            raise ValueError("Wheel metadata name or version is incorrect")
        requirements = metadata.get_all("Requires-Dist", [])
        if not any(item.startswith("pypdf") for item in requirements):
            raise ValueError("Wheel is missing the pypdf runtime dependency")
        if not any(item.startswith("mcp") for item in requirements):
            raise ValueError("Wheel is missing the mcp runtime dependency")
        entry_points = configparser.ConfigParser()
        entry_points.read_string(archive.read(f"psydcar-{version}.dist-info/entry_points.txt").decode())
        if entry_points["console_scripts"].get("psydcar") != "psydcar.cli:main":
            raise ValueError("Wheel is missing the Psydcar console entry point")

    with tarfile.open(sdist, "r:gz") as archive:
        prefix = f"psydcar-{version}/"
        members = {name.removeprefix(prefix) for name in archive.getnames() if name.startswith(prefix)}
        if any(".env" in Path(name).parts for name in members):
            raise ValueError("Source distribution contains a local .env file")
        expected = {"pyproject.toml", *(f"src/{name}" for name in REQUIRED_PACKAGE_FILES)}
        missing = expected - members
        if missing:
            raise ValueError(f"Source distribution is missing: {sorted(missing)}")
        pyproject = archive.extractfile(prefix + "pyproject.toml")
        if pyproject is None or tomllib.loads(pyproject.read().decode())["project"]["version"] != version:
            raise ValueError("Source distribution version is incorrect")


if __name__ == "__main__":
    try:
        check(sys.argv[1], Path(sys.argv[2]))
    except (IndexError, OSError, KeyError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise SystemExit(f"Invalid PyPI distribution: {exc}") from exc
