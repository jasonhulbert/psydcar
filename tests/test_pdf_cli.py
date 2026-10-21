from __future__ import annotations

import json
import os
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
except ImportError:
    PdfReader = PdfWriter = None
from psydcar.store import SidecarStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_pdf(path: Path, page_texts: list[str | None]) -> None:
    """Create a small PDF with real extractable text and optional blank pages."""
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
        })
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as target:
        writer.write(target)
    with path.open("rb") as source:
        reader = PdfReader(source)
        self_check = [page.extract_text() for page in reader.pages]
    for expected, actual in zip(page_texts, self_check):
        if expected is not None and expected not in actual:
            raise AssertionError("PDF test fixture did not contain extractable text")


@unittest.skipUnless(PdfReader is not None, "pypdf is not installed in this Python environment")
class PdfCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "store"
        self.work_one = self.root / "project-one"
        self.work_two = self.root / "project-two"
        self.source = self.root / "source"
        self.other_source = self.root / "other-source"
        for directory in (self.work_one, self.work_two, self.source, self.other_source):
            directory.mkdir()
        self.pdf = self.source / "guide.pdf"
        write_pdf(self.pdf, ["Solar archive comet", None, "Lunar archive orbit"])
        (self.source / "notes.md").write_text("Text archive nebula\n")
        (self.other_source / "other.md").write_text("Unrelated archive\n")
        self.assertEqual(self.cli("sidecar", "create", "Library", str(self.source)).returncode, 0)
        self.assertEqual(self.cli("sidecar", "create", "Other", str(self.other_source)).returncode, 0)

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

    def test_search_retrieve_page_and_persist_blank_page_notice(self) -> None:
        indexed = self.cli("sidecar", "index", "Library")
        self.assertEqual(indexed.returncode, 0, indexed.stderr)
        self.assertIn("guide.pdf, page 2: No searchable text", indexed.stdout)

        shown = self.cli("sidecar", "show", "Library", cwd=self.work_two)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Status: Indexed", shown.stdout)
        self.assertIn("guide.pdf, page 2: No searchable text", shown.stdout)

        first = self.cli("search", "--sidecar", "Library", "Solar", cwd=self.work_two)
        self.assertEqual(first.returncode, 0, first.stderr)
        hits = json.loads(first.stdout)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit["source"]["path"], str(self.pdf))
        self.assertEqual(hit["location"], {"kind": "page", "page": 1})
        self.assertEqual(hit["freshness"], "current")
        self.assertEqual(hit["context_origin"], "index")
        self.assertIn("Solar archive comet", hit["context"])

        last = json.loads(self.cli("search", "--sidecar", "Library", "Lunar").stdout)
        self.assertEqual(len(last), 1)
        self.assertEqual(last[0]["location"], {"kind": "page", "page": 3})
        retrieved = self.cli("retrieve", "--sidecar", "Library", hit["evidence_id"], cwd=self.work_two)
        self.assertEqual(retrieved.returncode, 0, retrieved.stderr)
        current = json.loads(retrieved.stdout)
        self.assertEqual(current["location"], {"kind": "page", "page": 1})
        self.assertEqual(current["context_origin"], "source")
        self.assertEqual(current["context"], hit["context"])

        outside = self.cli("retrieve", "--sidecar", "Other", hit["evidence_id"])
        self.assertEqual(outside.returncode, 2)
        self.assertIn("not found in the selected sidecars", outside.stderr)
        text_hit = json.loads(self.cli("search", "--sidecar", "Library", "nebula").stdout)
        self.assertEqual(text_hit[0]["location"], {"kind": "lines", "start": 1, "end": 1})

    def test_changed_missing_and_unreadable_pdf_withhold_context(self) -> None:
        self.assertEqual(self.cli("sidecar", "index", "Library").returncode, 0)
        hit = json.loads(self.cli("search", "--sidecar", "Library", "Solar").stdout)[0]
        evidence_id = hit["evidence_id"]

        write_pdf(self.pdf, ["Solar archive revised", None, "Lunar archive orbit"])
        stale_hit = json.loads(self.cli("search", "--sidecar", "Library", "comet").stdout)[0]
        self.assertEqual(stale_hit["freshness"], "changed")
        changed = json.loads(self.cli("retrieve", "--sidecar", "Library", evidence_id).stdout)
        self.assertEqual(changed["freshness"], "changed")
        self.assertIsNone(changed["context"])

        self.pdf.unlink()
        missing = json.loads(self.cli("retrieve", "--sidecar", "Library", evidence_id).stdout)
        self.assertEqual(missing["freshness"], "missing")
        self.assertIsNone(missing["context"])

        self.pdf.write_bytes(b"not a PDF")
        unreadable = json.loads(self.cli("retrieve", "--sidecar", "Library", evidence_id).stdout)
        self.assertEqual(unreadable["freshness"], "unreadable")
        self.assertIsNone(unreadable["context"])

    def test_reindex_clears_old_pdf_notices(self) -> None:
        self.assertEqual(self.cli("sidecar", "index", "Library").returncode, 0)
        write_pdf(self.pdf, ["Solar archive comet", "Now page two has text", "Lunar archive orbit"])
        refreshed = self.cli("sidecar", "index", "Library")
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertNotIn("No searchable text", self.cli("sidecar", "show", "Library").stdout)
        page_two = json.loads(self.cli("search", "--sidecar", "Library", "page two").stdout)
        self.assertEqual(page_two[0]["location"], {"kind": "page", "page": 2})

    def test_existing_text_index_survives_pdf_schema_upgrade(self) -> None:
        sidecar = SidecarStore(self.store / "psydcar.sqlite3").get("Library")
        text_path = self.source / "notes.md"
        old_id = str(uuid.uuid4())
        with sqlite3.connect(self.store / "psydcar.sqlite3") as connection:
            connection.executescript(
                """CREATE TABLE documents (
                    id INTEGER PRIMARY KEY, sidecar_id TEXT NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, indexed_at TEXT NOT NULL,
                    UNIQUE(sidecar_id, relative_path)
                );
                CREATE TABLE passages (
                    id TEXT PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, context TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE passages_fts USING fts5(evidence_id UNINDEXED, context, tokenize='unicode61');"""
            )
            document = connection.execute(
                "INSERT INTO documents (sidecar_id, relative_path, sha256, indexed_at) VALUES (?, ?, ?, ?)",
                (sidecar.id, "notes.md", hashlib.sha256(text_path.read_bytes()).hexdigest(), "2026-01-01T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO passages (id, document_id, start_line, end_line, context) VALUES (?, ?, ?, ?, ?)",
                (old_id, document.lastrowid, 1, 1, "Text archive nebula"),
            )
            connection.execute(
                "INSERT INTO passages_fts (evidence_id, context) VALUES (?, ?)", (old_id, "Text archive nebula")
            )
        hits = json.loads(self.cli("search", "--sidecar", "Library", "nebula").stdout)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["evidence_id"], old_id)
        self.assertEqual(hits[0]["location"], {"kind": "lines", "start": 1, "end": 1})
        self.assertEqual(json.loads(self.cli("retrieve", "--sidecar", "Library", old_id).stdout)["freshness"], "current")


if __name__ == "__main__":
    unittest.main()
