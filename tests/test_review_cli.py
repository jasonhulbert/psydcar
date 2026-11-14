from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from git_sandbox import git, use_sandbox
from psydcar.evidence import EvidenceStore
from psydcar.proposal_files import ProposalFiles
from psydcar.proposals import Proposals
from psydcar.registry import SidecarStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReviewCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store_dir = self.root / "store"
        self.source = self.root / "notes"
        self.source.mkdir()
        self.note = self.source / "heron.md"
        self.note.write_text("The heron waits by the reeds.\n")
        self.store = SidecarStore(self.store_dir / "state.sqlite3")
        self.sidecar = self.store.create("Notes", self.source)
        EvidenceStore(self.store).update(self.sidecar.id)

    def propose(self, content: str, summary: str) -> str:
        base = ProposalFiles(self.store).files(self.sidecar.id).read("heron.md")
        return Proposals(self.store).submit(
            self.sidecar.id,
            "heron.md",
            base["version"],
            content,
            summary,
            "Review assistant",
        )["id"]

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PSYDCAR_HOME"] = str(self.store_dir)
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run([sys.executable, "-m", "psydcar", *args], cwd=self.root, env=environment, capture_output=True, text=True, check=False)

    def test_owner_reviews_and_approves_a_proposal_that_becomes_searchable(self) -> None:
        proposal_id = self.propose("The heron waits by the reeds.\nA kingfisher flashes past.\n", "Add the kingfisher.")

        self.assertIn("Add the kingfisher.", self.cli("proposal", "list").stdout)
        self.assertIn("+A kingfisher flashes past.", self.cli("proposal", "show", proposal_id[:8]).stdout)
        approved = self.cli("proposal", "approve", proposal_id[:8])
        self.assertEqual(approved.returncode, 0, approved.stderr)

        results = json.loads(self.cli("search", "--sidecar", "Notes", "kingfisher").stdout)
        self.assertEqual([result["source"]["relative_path"] for result in results], ["heron.md"])

    def test_owner_rejects_a_proposal(self) -> None:
        proposal_id = self.propose("Replaced text.\n", "Replace the note.")

        rejected = self.cli("proposal", "reject", proposal_id)

        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertEqual(self.note.read_text(), "The heron waits by the reeds.\n")
        self.assertIn("Rejected", self.cli("proposal", "list", "Notes", "--reviewed").stdout)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_approval_with_the_git_record_on_commits_only_the_approved_file(self) -> None:
        use_sandbox(self, self.root)
        (self.source / "todo.md").write_text("Buy bread.\n")
        git(self.source, "init", "-q")
        git(self.source, "add", ".")
        git(self.source, "commit", "-q", "-m", "Start")
        (self.source / "todo.md").write_text("Buy bread and milk.\n")
        git(self.source, "add", "todo.md")
        self.assertEqual(self.cli("sidecar", "git-record", "Notes", "on").returncode, 0)
        proposal_id = self.propose("The heron waits by the reeds.\nA kingfisher flashes past.\n", "Add the kingfisher.")

        approved = self.cli("proposal", "approve", proposal_id)

        self.assertIn("Recorded commit", approved.stdout, approved.stderr)
        commit = git(self.source, "show", "--format=%B", "--name-only", "HEAD")
        self.assertTrue(commit.startswith("Add the kingfisher.") and "Review assistant" in commit and commit.endswith("\nheron.md"), commit)
        self.assertEqual(git(self.source, "status", "--porcelain"), "M  todo.md")


if __name__ == "__main__":
    unittest.main()
