from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from psydcar.evidence import EvidenceStore
from psydcar.store import SidecarStore
from psydcar.watcher import SidecarWatcher

try:
    from test_pdf_cli import PdfReader, write_pdf
except ImportError:
    PdfReader = None


class WatcherCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store_dir = self.root / "store"
        self.source = self.root / "source"
        self.work = self.root / "other-project"
        self.source.mkdir()
        self.work.mkdir()
        (self.source / "base.md").write_text("mango pear\n")
        (self.source / "remove.py").write_text("sunrise_removed = True\n")
        self.assertEqual(self.cli("sidecar", "create", "Library", str(self.source)).returncode, 0)

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store_dir)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return env

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "psydcar", *args], cwd=self.work,
            env=self.environment(), capture_output=True, text=True, check=False,
        )

    def hits(self, query: str) -> list[dict]:
        result = self.cli("search", "--sidecar", "Library", query)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def wait_until(self, condition, message: str) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.1)
        self.fail(message)

    def test_running_watcher_converges_after_close_add_change_delete_events(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-m", "psydcar", "watch", "--interval", "0.1", "--reconcile-interval", "0.5"],
            cwd=self.work, env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.wait_until(lambda: bool(self.hits("pear")), "startup reconciliation did not index source")
            for content in ("mango lime\n", "mango kiwi\n", "mango plum\n"):
                (self.source / "base.md").write_text(content)
            (self.source / "added.md").write_text("aurora added\n")
            (self.source / "remove.py").unlink()
            self.wait_until(
                lambda: bool(self.hits("plum")) and bool(self.hits("aurora"))
                and not self.hits("pear") and not self.hits("sunrise_removed"),
                "watcher did not converge to the final files",
            )
            self.assertEqual(self.hits("plum")[0]["freshness"], "current")
            self.assertEqual(self.hits("plum")[0]["source"]["relative_path"], "base.md")
        finally:
            process.terminate()
            stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("Watch startup", stdout)
        self.assertIn("Watch source_changed", stdout)

    def test_watch_once_catches_up_after_restart_and_keeps_selection_scoped(self) -> None:
        first = self.cli("watch", "--once")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(self.hits("pear"))
        (self.source / "base.md").write_text("mango plum\n")
        (self.source / "new.md").write_text("restart aurora\n")
        (self.source / "remove.py").unlink()
        with sqlite3.connect(self.store_dir / "psydcar.sqlite3") as connection:
            connection.execute("UPDATE sidecars SET status = 'update_pending'")
            connection.execute("UPDATE sidecar_updates SET completed_at = NULL")
        stale = self.hits("pear")[0]
        self.assertEqual(stale["freshness"], "changed")
        self.assertEqual(stale["index_freshness"], "stale")
        self.assertEqual(stale["processing"]["state"], "pending")

        restarted = self.cli("watch", "--once")
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertIn("1 added, 1 changed, 1 deleted", restarted.stdout)
        self.assertTrue(self.hits("plum"))
        self.assertTrue(self.hits("aurora"))
        self.assertFalse(self.hits("pear"))
        self.assertFalse(self.hits("sunrise_removed"))
        self.assertIn("Source attention", self.cli("sidecar", "show", "Library").stdout)

    def test_scope_periodic_reconciliation_and_error_recovery(self) -> None:
        store = SidecarStore(self.store_dir / "psydcar.sqlite3")
        watcher = SidecarWatcher(store, interval=0.05, reconcile_interval=10)
        initial = watcher.step()
        self.assertEqual(len(initial), 1)
        self.assertEqual(initial[0].reason, "startup")
        (self.source / ".hidden.md").write_text("ignored hidden\n")
        (self.source / "notes.csv").write_text("ignored extension\n")
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules" / "ignored.js").write_text("ignored directory\n")
        self.assertEqual(watcher.step(), [])

        (self.source / "base.md").write_text("mango plum\n")
        changed = watcher.step()
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].reason, "source_changed")
        self.assertEqual(changed[0].report.changed, 1)
        self.assertEqual(EvidenceStore(store).search(["Library"], "plum")[0]["freshness"], "current")

        # Simulate a metadata check that missed a change. The full pass still compares bytes.
        (self.source / "base.md").write_text("mango kiwi\n")
        sidecar = store.get("Library")
        with patch("psydcar.watcher.source_snapshot", return_value=watcher._snapshots[sidecar.id]):
            watcher._last_reconcile = time.monotonic() - 11
            periodic = watcher.step()
        self.assertEqual(periodic[0].reason, "periodic")
        self.assertEqual(periodic[0].report.changed, 1)
        self.assertEqual(EvidenceStore(store).search(["Library"], "kiwi")[0]["freshness"], "current")

        self.source.rename(self.root / "moved-source")
        failed = watcher.step()
        self.assertEqual(len(failed), 1)
        self.assertIn("missing", failed[0].error)
        self.assertEqual(EvidenceStore(store).update_state("Library")["state"], "attention")
        missing_hit = EvidenceStore(store).search(["Library"], "kiwi")[0]
        self.assertEqual(missing_hit["freshness"], "missing")
        self.assertEqual(missing_hit["index_freshness"], "stale")
        (self.root / "moved-source").rename(self.source)
        recovered = watcher.step()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].report.sidecar.status, "indexed")

    @unittest.skipUnless(PdfReader is not None, "pypdf is not installed in this Python environment")
    def test_pdf_events_use_the_shared_update_path(self) -> None:
        store = SidecarStore(self.store_dir / "psydcar.sqlite3")
        watcher = SidecarWatcher(store, interval=0.05, reconcile_interval=10)
        watcher.step()
        pdf = self.source / "guide.pdf"
        write_pdf(pdf, ["solar archive"])
        added = watcher.step()[0]
        self.assertEqual(added.report.added, 1)
        first = self.hits("solar")[0]
        self.assertEqual(first["location"], {"kind": "page", "page": 1})

        write_pdf(pdf, ["lunar archive"])
        changed = watcher.step()[0]
        self.assertEqual(changed.report.changed, 1)
        self.assertFalse(self.hits("solar"))
        self.assertEqual(self.hits("lunar")[0]["freshness"], "current")

        pdf.unlink()
        deleted = watcher.step()[0]
        self.assertEqual(deleted.report.deleted, 1)
        self.assertEqual(deleted.report.sidecar.status, "source_attention")
        self.assertFalse(self.hits("lunar"))
        self.assertEqual(self.cli("retrieve", "--sidecar", "Library", first["evidence_id"]).returncode, 2)


if __name__ == "__main__":
    unittest.main()
