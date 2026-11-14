"""Run git in tests without reading or changing the developer's git configuration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def use_sandbox(test: TestCase, root: Path) -> None:
    """Point git at a configuration inside the test's temporary folder for the rest of the test.

    Child processes inherit it. Git also stops looking for a repository at that folder.
    """
    config = root / "gitconfig"
    config.write_text(
        "[user]\n\tname = Owner\n\temail = owner@example.com\n"
        "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
    )
    environment = patch.dict(os.environ, {
        "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CEILING_DIRECTORIES": str(root),
    })
    environment.start()
    test.addCleanup(environment.stop)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
