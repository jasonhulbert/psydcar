from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UpdateCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "store"
        self.source = self.root / "source"
        self.other_work = self.root / "other-work"
        self.source.mkdir()
        self.other_work.mkdir()
        (self.source / "changed.md").write_text("old amber record\n")
        (self.source / "deleted.md").write_text("obsolete amber record\n")
        (self.source / "stable.md").write_text("stable amber record\n")
        self.assertEqual(self.cli("sidecar", "create", "Library", str(self.source)).returncode, 0)
        self.assertEqual(self.cli("sidecar", "index", "Library").returncode, 0)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "psydcar", *args], cwd=self.other_work,
            env=env, capture_output=True, text=True, check=False,
        )

    def search(self, term: str) -> list[dict]:
        result = self.cli("search", "--sidecar", "Library", term)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_update_detects_changes_and_keeps_unchanged_ids(self) -> None:
        original = {hit["source"]["relative_path"]: hit for hit in self.search("amber")}
        (self.source / "changed.md").write_text("new violet record\n")
        (self.source / "deleted.md").unlink()
        (self.source / "added.md").write_text("fresh violet record\n")

        updated = self.cli("sidecar", "update", "Library")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertIn("Changes: 1 added, 1 changed, 1 deleted, 1 unchanged", updated.stdout)
        self.assertIn("Status: Source attention", self.cli("sidecar", "show", "Library").stdout)
        self.assertIn("Update state: Needs attention", self.cli("sidecar", "show", "Library").stdout)
        self.assertIn("Needs attention: Removed source: deleted.md", self.cli("sidecar", "show", "Library").stdout)

        violet = {hit["source"]["relative_path"]: hit for hit in self.search("violet")}
        self.assertEqual(set(violet), {"added.md", "changed.md"})
        self.assertEqual({hit["freshness"] for hit in violet.values()}, {"current"})
        amber = {hit["source"]["relative_path"]: hit for hit in self.search("amber")}
        self.assertEqual(set(amber), {"stable.md"})
        self.assertEqual(amber["stable.md"]["evidence_id"], original["stable.md"]["evidence_id"])
        for name in ("changed.md", "deleted.md"):
            old = self.cli("retrieve", "--sidecar", "Library", original[name]["evidence_id"])
            self.assertEqual(old.returncode, 2)

        cleared = self.cli("sidecar", "update", "Library")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertIn("Update state: Current", self.cli("sidecar", "show", "Library").stdout)
        self.assertNotIn("Needs attention:", self.cli("sidecar", "show", "Library").stdout)

    def test_failed_update_retains_stale_search_and_recovery_clears_attention(self) -> None:
        original = {hit["source"]["relative_path"]: hit for hit in self.search("amber")}
        (self.source / "changed.md").write_bytes(b"\xff\xfe")
        (self.source / "deleted.md").unlink()
        (self.source / "added.md").write_text("fresh violet record\n")

        failed = self.cli("sidecar", "update", "Library")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("Skipped: changed.md", failed.stderr)
        self.assertIn("Changes: 1 added, 0 changed, 1 deleted, 1 unchanged", failed.stdout)
        show = self.cli("sidecar", "show", "Library")
        self.assertIn("Update state: Needs attention", show.stdout)
        self.assertIn("Needs attention: changed.md", show.stdout)
        self.assertIn("Last successful update:", show.stdout)

        old = next(hit for hit in self.search("old") if hit["source"]["relative_path"] == "changed.md")
        self.assertEqual(old["evidence_id"], original["changed.md"]["evidence_id"])
        self.assertEqual(old["freshness"], "unreadable")
        self.assertEqual(old["index_freshness"], "stale")
        self.assertEqual(old["processing"]["state"], "attention")
        retrieved = json.loads(self.cli("retrieve", "--sidecar", "Library", old["evidence_id"]).stdout)
        self.assertEqual(retrieved["freshness"], "unreadable")
        self.assertIsNone(retrieved["context"])
        self.assertEqual(self.cli("retrieve", "--sidecar", "Library", original["deleted.md"]["evidence_id"]).returncode, 2)

        (self.source / "changed.md").write_text("repaired violet record\n")
        recovered = self.cli("sidecar", "update", "Library")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("Update state: Current", self.cli("sidecar", "show", "Library").stdout)
        self.assertNotIn("Needs attention:", self.cli("sidecar", "show", "Library").stdout)
        self.assertEqual({hit["source"]["relative_path"] for hit in self.search("violet")}, {"added.md", "changed.md"})

    def test_missing_source_tree_preserves_search_but_retrieval_reports_missing(self) -> None:
        original = self.search("amber")[0]
        self.source.rename(self.root / "moved-source")
        update = self.cli("sidecar", "update", "Library")
        self.assertEqual(update.returncode, 2)
        show = self.cli("sidecar", "show", "Library")
        self.assertIn("Update state: Needs attention", show.stdout)
        hits = self.search("amber")
        self.assertTrue(hits)
        self.assertEqual({hit["freshness"] for hit in hits}, {"missing"})
        self.assertEqual({hit["index_freshness"] for hit in hits}, {"stale"})
        retrieved = json.loads(self.cli("retrieve", "--sidecar", "Library", original["evidence_id"]).stdout)
        self.assertEqual(retrieved["freshness"], "missing")
        self.assertIsNone(retrieved["context"])


if __name__ == "__main__":
    unittest.main()
