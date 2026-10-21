from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvidenceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "store"
        self.work_one = self.root / "project-one"
        self.work_two = self.root / "project-two"
        self.work_one.mkdir()
        self.work_two.mkdir()
        self.source_a = self.root / "source-a"
        self.source_b = self.root / "source-b"
        self.source_a.mkdir()
        self.source_b.mkdir()
        (self.source_a / "guide.md").write_text("Title\nThe blue comet marks this source.\nLast line\n")
        (self.source_b / "guide.md").write_text("Title\nThe red comet marks another source.\n")
        (self.source_a / "nested").mkdir()
        (self.source_a / "nested" / "sample.py").write_text("def orbit():\n    return 'blue comet'\n")
        (self.source_a / ".hidden.md").write_text("secret comet")
        (self.source_a / "node_modules").mkdir()
        (self.source_a / "node_modules" / "ignored.js").write_text("secret comet")
        (self.source_a / "unsupported.csv").write_text("secret comet")
        self.assertEqual(self.cli("sidecar", "create", "A", str(self.source_a)).returncode, 0)
        self.assertEqual(self.cli("sidecar", "create", "B", str(self.source_b)).returncode, 0)
        self.assertEqual(self.cli("sidecar", "index", "A").returncode, 0)
        self.assertEqual(self.cli("sidecar", "index", "B").returncode, 0)

    def cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "psydcar", *args],
            cwd=cwd or self.work_one,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_selection_locations_restart_and_ignore_rules(self) -> None:
        no_selection = self.cli("search", "comet")
        self.assertEqual(no_selection.returncode, 2)
        self.assertIn("--sidecar", no_selection.stderr)

        result = self.cli("search", "--sidecar", "A", "comet", cwd=self.work_two)
        self.assertEqual(result.returncode, 0, result.stderr)
        hits = json.loads(result.stdout)
        self.assertEqual({hit["sidecar"]["name"] for hit in hits}, {"A"})
        self.assertEqual({hit["source"]["relative_path"] for hit in hits}, {"guide.md", "nested/sample.py"})
        guide = next(hit for hit in hits if hit["source"]["relative_path"] == "guide.md")
        self.assertEqual(guide["location"], {"kind": "lines", "start": 1, "end": 3})
        self.assertEqual(guide["freshness"], "current")
        self.assertEqual(guide["context_origin"], "index")
        self.assertIn("blue comet", guide["context"])

        retrieved = self.cli("retrieve", "--sidecar", "A", guide["evidence_id"], cwd=self.work_two)
        self.assertEqual(retrieved.returncode, 0, retrieved.stderr)
        current = json.loads(retrieved.stdout)
        self.assertEqual(current["context_origin"], "source")
        self.assertEqual(current["context"], guide["context"])
        self.assertEqual(current["source"]["path"], str(self.source_a / "guide.md"))
        by_id = self.cli("retrieve", "--sidecar", guide["sidecar"]["id"], guide["evidence_id"])
        self.assertEqual(by_id.returncode, 0, by_id.stderr)

        wrong_sidecar = self.cli("retrieve", "--sidecar", "B", guide["evidence_id"])
        self.assertEqual(wrong_sidecar.returncode, 2)
        self.assertIn("not found in the selected sidecars", wrong_sidecar.stderr)
        both = self.cli("search", "--sidecar", "A", "--sidecar", "B", "comet")
        self.assertEqual({hit["sidecar"]["name"] for hit in json.loads(both.stdout)}, {"A", "B"})

    def test_changed_missing_and_unreadable_sources_withhold_old_context(self) -> None:
        hit = json.loads(self.cli("search", "--sidecar", "A", "blue comet").stdout)[0]
        evidence_id = hit["evidence_id"]
        path = self.source_a / hit["source"]["relative_path"]
        original = path.read_bytes()

        path.write_bytes(original.replace(b"\n", b"\r\n"))
        changed_search = json.loads(self.cli("search", "--sidecar", "A", "blue comet").stdout)
        matching = next(item for item in changed_search if item["evidence_id"] == evidence_id)
        self.assertEqual(matching["freshness"], "changed")
        self.assertEqual(matching["index_freshness"], "stale")
        self.assertEqual(matching["processing"]["state"], "current")
        changed = json.loads(self.cli("retrieve", "--sidecar", "A", evidence_id).stdout)
        self.assertEqual(changed["freshness"], "changed")
        self.assertIsNone(changed["context"])
        self.assertIsNone(changed["context_origin"])

        path.unlink()
        missing = json.loads(self.cli("retrieve", "--sidecar", "A", evidence_id).stdout)
        self.assertEqual(missing["freshness"], "missing")
        self.assertIsNone(missing["context"])

        path.write_bytes(b"\xff\xfe")
        unreadable = json.loads(self.cli("retrieve", "--sidecar", "A", evidence_id).stdout)
        self.assertEqual(unreadable["freshness"], "unreadable")
        self.assertIsNone(unreadable["context"])

    def test_reindex_replaces_old_evidence_and_removal_cleans_index(self) -> None:
        previous = json.loads(self.cli("search", "--sidecar", "A", "blue comet").stdout)
        old_id = next(hit["evidence_id"] for hit in previous if hit["source"]["relative_path"] == "guide.md")
        unchanged_id = next(hit["evidence_id"] for hit in previous if hit["source"]["relative_path"] == "nested/sample.py")
        (self.source_a / "guide.md").write_text("Title\nNow green comet.\n")
        refreshed = self.cli("sidecar", "index", "A", cwd=self.work_two)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        blue_hits = json.loads(self.cli("search", "--sidecar", "A", "blue").stdout)
        self.assertTrue(blue_hits)
        self.assertNotIn("guide.md", {hit["source"]["relative_path"] for hit in blue_hits})
        old = self.cli("retrieve", "--sidecar", "A", old_id)
        self.assertEqual(old.returncode, 2)
        self.assertEqual(self.cli("retrieve", "--sidecar", "A", unchanged_id).returncode, 0)
        new = json.loads(self.cli("search", "--sidecar", "A", "green").stdout)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["freshness"], "current")

        self.assertEqual(self.cli("sidecar", "remove", "A").returncode, 0)
        self.assertTrue((self.source_a / "guide.md").exists())
        self.assertEqual(self.cli("search", "--sidecar", "A", "green").returncode, 2)
        with sqlite3.connect(self.store / "psydcar.sqlite3") as connection:
            indexed_sidecar_ids = {
                row[0] for row in connection.execute("SELECT DISTINCT sidecar_id FROM documents")
            }
            self.assertEqual(len(indexed_sidecar_ids), 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM passages_fts").fetchone()[0], 1)

    def test_partial_index_failure_is_reported(self) -> None:
        (self.source_a / "broken.txt").write_bytes(b"\xff\xfe")
        indexed = self.cli("sidecar", "index", "A")
        self.assertEqual(indexed.returncode, 1)
        self.assertIn("Skipped: broken.txt", indexed.stderr)
        self.assertIn("Status: Index error", self.cli("sidecar", "show", "A").stdout)
        good_hits = json.loads(self.cli("search", "--sidecar", "A", "comet").stdout)
        self.assertTrue(good_hits)


if __name__ == "__main__":
    unittest.main()
