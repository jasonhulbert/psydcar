from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from psydcar.store import INITIAL_STATUS, SidecarStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SidecarCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store_dir = self.root / "managed-store"
        self.first_workdir = self.root / "project-one"
        self.second_workdir = self.root / "project-two"
        self.first_workdir.mkdir()
        self.second_workdir.mkdir()
        self.source_one = self.root / "reference-one"
        self.source_two = self.root / "reference-two"
        self.source_one.mkdir()
        self.source_two.mkdir()
        (self.source_one / "keep.txt").write_text("source stays authoritative\n")
        (self.source_two / "keep.txt").write_text("second source\n")

    def cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store_dir)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "psydcar", "sidecar", *args],
            cwd=cwd or self.first_workdir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_create_rename_and_remove_persist_across_working_directories(self) -> None:
        first = self.cli("create", "Personal notes", str(self.source_one))
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.cli("create", "API docs", str(self.source_two), cwd=self.second_workdir)
        self.assertEqual(second.returncode, 0, second.stderr)

        store = SidecarStore(self.store_dir / "psydcar.sqlite3")
        before = store.get("Personal notes")
        self.assertEqual(before.status, INITIAL_STATUS)
        self.assertEqual(before.source_path, self.source_one)

        listing = self.cli("list", cwd=self.second_workdir)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        for expected in ("Personal notes", "API docs", str(self.source_one), str(self.source_two), "Not indexed", before.id):
            self.assertIn(expected, listing.stdout)

        renamed = self.cli("rename", before.id, "Research", cwd=self.second_workdir)
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        self.assertIn(before.id, renamed.stdout)
        inspected = self.cli("show", "Research")
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertIn(f"ID: {before.id}", inspected.stdout)
        self.assertIn(f"Source: {self.source_one}", inspected.stdout)
        self.assertIn("Status: Not indexed", inspected.stdout)

        removed = self.cli("remove", "Research", cwd=self.second_workdir)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("Source files were not changed", removed.stdout)
        self.assertEqual((self.source_one / "keep.txt").read_text(), "source stays authoritative\n")
        self.assertEqual([item.name for item in SidecarStore(self.store_dir / "psydcar.sqlite3").list()], ["API docs"])

    def test_invalid_inputs_are_clear_and_do_not_create_records(self) -> None:
        missing = self.cli("create", "Missing", str(self.root / "absent"))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("Source directory does not exist", missing.stderr)

        file_source = self.cli("create", "File", str(self.source_one / "keep.txt"))
        self.assertEqual(file_source.returncode, 2)
        self.assertIn("Source must be a directory", file_source.stderr)

        blank = self.cli("create", " ", str(self.source_one))
        self.assertEqual(blank.returncode, 2)
        self.assertIn("Sidecar name must be nonempty", blank.stderr)

        good = self.cli("create", "Docs", str(self.source_one))
        self.assertEqual(good.returncode, 0, good.stderr)
        duplicate = self.cli("create", "docs", str(self.source_two))
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("already exists", duplicate.stderr)

        unknown = self.cli("show", "unknown")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("No sidecar named or identified", unknown.stderr)
        self.assertEqual(len(SidecarStore(self.store_dir / "psydcar.sqlite3").list()), 1)


if __name__ == "__main__":
    unittest.main()
