from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from test_pdf_cli import write_pdf
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET = "VERY_SECRET_TEST_CREDENTIAL"


def deterministic_vector(text: str) -> list[float]:
    lower = text.lower()
    if any(word in lower for word in ("automobile", "car", "engine")):
        return [1.0, 0.0, 0.0]
    if any(word in lower for word in ("astronomy", "orbit", "planet")):
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


class GatewayHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        state = self.server.state
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        state["requests"].append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": body,
        })
        if state["fail_on_request"] == len(state["requests"]):
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"temporary failure")
            return
        if state["bad_encoding_on_request"] == len(state["requests"]):
            payload = b"\xff"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if state["pause_on_request"] == len(state["requests"]):
            state["pause_entered"].set()
            state["resume"].wait(10)
        result = {"data": [
            {"index": index, "embedding": deterministic_vector(text)}
            for index, text in enumerate(body["input"])
        ]}
        payload = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@unittest.skipUnless(PdfReader is not None, "pypdf is not installed in this Python environment")
class EmbeddingCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "store"
        self.source = self.root / "source"
        self.other_source = self.root / "other-source"
        self.work_one = self.root / "project-one"
        self.work_two = self.root / "project-two"
        for directory in (self.source, self.other_source, self.work_one, self.work_two):
            directory.mkdir()
        (self.source / "cars.md").write_text("A car engine is documented here.\n")
        (self.source / "orbit.py").write_text("def orbit():\n    return 'planet'\n")
        write_pdf(self.source / "guide.pdf", ["PDF astronomy orbit guide"])
        (self.other_source / "private.md").write_text("Automobile private detail\n")
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        except PermissionError:
            self.skipTest("loopback sockets are unavailable in this sandbox")
        self.server.state = {
            "requests": [], "fail_on_request": None, "bad_encoding_on_request": None,
            "pause_on_request": None, "pause_entered": threading.Event(), "resume": threading.Event(),
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/gateway/v1"
        self.assertEqual(self.cli("sidecar", "create", "Library", str(self.source)).returncode, 0)
        self.assertEqual(self.cli("sidecar", "create", "Private", str(self.other_source)).returncode, 0)

    def cli(self, *args: str, cwd: Path | None = None, credential: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        if credential:
            env["PSYDCAR_TEST_KEY"] = SECRET
        else:
            env.pop("PSYDCAR_TEST_KEY", None)
        return subprocess.run(
            [sys.executable, "-m", "psydcar", *args], cwd=cwd or self.work_one,
            env=env, capture_output=True, text=True, check=False,
        )

    def configure(self) -> None:
        configured = self.cli(
            "provider", "set", "--base-url", self.base_url,
            "--model", "deterministic-test", "--api-key-env", "PSYDCAR_TEST_KEY",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

    def test_explicit_configuration_gateway_hybrid_ranking_and_no_secret_output(self) -> None:
        before = self.cli("sidecar", "index", "Library")
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertIn("Embeddings: Not configured", before.stdout)
        self.assertEqual(self.server.state["requests"], [])

        self.configure()
        self.assertEqual(self.server.state["requests"], [])
        shown = self.cli("provider", "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn(self.base_url, shown.stdout)
        self.assertIn("Credential available: Yes", shown.stdout)
        self.assertNotIn(SECRET, shown.stdout + shown.stderr)

        indexed = self.cli("sidecar", "index", "Library", cwd=self.work_two)
        self.assertEqual(indexed.returncode, 0, indexed.stderr)
        self.assertIn("Embeddings: Complete (3/3)", indexed.stdout)
        requests = self.server.state["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["path"], "/gateway/v1/embeddings")
        self.assertEqual(requests[0]["authorization"], f"Bearer {SECRET}")
        self.assertEqual(requests[0]["body"]["model"], "deterministic-test")
        self.assertNotIn(SECRET.encode(), (self.store / "psydcar.sqlite3").read_bytes())
        inputs = requests[0]["body"]["input"]
        self.assertEqual(len(inputs), 3)
        self.assertTrue(any("car engine" in item for item in inputs))
        self.assertTrue(any("def orbit" in item for item in inputs))
        self.assertTrue(any("PDF astronomy" in item for item in inputs))

        search = self.cli("search", "--sidecar", "Library", "automobile", cwd=self.work_two)
        self.assertEqual(search.returncode, 0, search.stderr)
        hits = json.loads(search.stdout)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"]["relative_path"], "cars.md")
        self.assertEqual(hits[0]["search"]["mode"], "hybrid")
        self.assertIsNone(hits[0]["search"]["lexical_rank"])
        self.assertEqual(hits[0]["search"]["semantic_rank"], 1)
        self.assertEqual({hit["sidecar"]["name"] for hit in hits}, {"Library"})
        self.assertNotIn(SECRET, search.stdout + search.stderr)

        self.assertEqual(self.cli("sidecar", "index", "Private").returncode, 0)
        library_only = json.loads(self.cli("search", "--sidecar", "Library", "automobile").stdout)
        self.assertEqual({hit["sidecar"]["name"] for hit in library_only}, {"Library"})
        combined = json.loads(self.cli("search", "--sidecar", "Library", "--sidecar", "Private", "automobile").stdout)
        self.assertEqual({hit["sidecar"]["name"] for hit in combined}, {"Library", "Private"})

        pdf_search = self.cli("search", "--sidecar", "Library", "astronomy")
        pdf_hits = json.loads(pdf_search.stdout)
        self.assertTrue(any(hit["source"]["relative_path"] == "guide.pdf" and hit["location"]["page"] == 1 for hit in pdf_hits if hit["location"]["kind"] == "page"))

        cleared = self.cli("provider", "clear")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        with sqlite3.connect(self.store / "psydcar.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM passage_embeddings").fetchone()[0], 0)
        lexical = self.cli("search", "--sidecar", "Library", "car")
        self.assertEqual(json.loads(lexical.stdout)[0]["search"]["mode"], "lexical")
        self.assertEqual(self.cli("provider", "show").stdout.strip(), "Embedding provider: Not configured")

    def test_partial_provider_failure_is_visible_and_lexical_evidence_survives(self) -> None:
        for index in range(17):
            (self.source / f"extra-{index:02}.md").write_text(f"car engine record {index}\n")
        self.configure()
        self.server.state["fail_on_request"] = 2
        indexed = self.cli("sidecar", "index", "Library")
        self.assertEqual(indexed.returncode, 1)
        self.assertIn("Embedding provider returned HTTP 503", indexed.stderr)
        self.assertIn("lexical evidence remains available", indexed.stderr)
        show = self.cli("sidecar", "show", "Library")
        self.assertIn("Status: Embedding error", show.stdout)
        self.assertIn("Embeddings: Error (16/20)", show.stdout)
        search = self.cli("search", "--sidecar", "Library", "engine")
        self.assertEqual(search.returncode, 0, search.stderr)
        self.assertTrue(json.loads(search.stdout))
        self.assertEqual(json.loads(search.stdout)[0]["search"]["mode"], "lexical")
        self.assertIn("Embeddings are pending or incomplete", search.stderr)
        self.assertNotIn(SECRET, indexed.stdout + indexed.stderr + show.stdout + search.stdout + search.stderr)

        self.server.state["fail_on_request"] = None
        recovered = self.cli("sidecar", "index", "Library")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("Embeddings: Complete (20/20)", recovered.stdout)
        self.assertIn("Status: Indexed", self.cli("sidecar", "show", "Library").stdout)

    def test_query_provider_failure_falls_back_to_lexical(self) -> None:
        self.configure()
        self.assertEqual(self.cli("sidecar", "index", "Library").returncode, 0)
        self.server.state["fail_on_request"] = len(self.server.state["requests"]) + 1
        search = self.cli("search", "--sidecar", "Library", "car")
        self.assertEqual(search.returncode, 0, search.stderr)
        hits = json.loads(search.stdout)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["search"]["mode"], "lexical")
        self.assertIn("HTTP 503", search.stderr)
        self.assertNotIn(SECRET, search.stdout + search.stderr)

    def test_non_utf8_provider_response_marks_embedding_error_and_preserves_lexical_evidence(self) -> None:
        self.configure()
        self.server.state["bad_encoding_on_request"] = 1
        indexed = self.cli("sidecar", "index", "Library")
        self.assertEqual(indexed.returncode, 1)
        self.assertIn("non-UTF-8 response", indexed.stderr)
        self.assertIn("lexical evidence remains available", indexed.stderr)
        self.assertNotIn("Traceback", indexed.stderr)

        shown = self.cli("sidecar", "show", "Library")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Status: Embedding error", shown.stdout)
        self.assertIn("Embeddings: Error (0/3)", shown.stdout)

        search = self.cli("search", "--sidecar", "Library", "car")
        self.assertEqual(search.returncode, 0, search.stderr)
        hits = json.loads(search.stdout)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["search"]["mode"], "lexical")
        self.assertNotIn(SECRET, indexed.stdout + indexed.stderr + shown.stdout + search.stdout + search.stderr)

    def test_pending_update_marks_search_stale_and_refreshes_embeddings(self) -> None:
        self.configure()
        self.assertEqual(self.cli("sidecar", "index", "Library").returncode, 0)
        old = next(hit for hit in json.loads(self.cli("search", "--sidecar", "Library", "car").stdout)
                   if hit["source"]["relative_path"] == "cars.md")
        (self.source / "cars.md").write_text("An automobile engine is documented here.\n")
        (self.source / "orbit.py").unlink()
        (self.source / "new.md").write_text("A new car reference is here.\n")
        self.server.state["pause_on_request"] = len(self.server.state["requests"]) + 1
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["PSYDCAR_TEST_KEY"] = SECRET
        process = subprocess.Popen(
            [sys.executable, "-m", "psydcar", "sidecar", "update", "Library"],
            cwd=self.work_two, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertTrue(self.server.state["pause_entered"].wait(5), "update did not reach the gateway")
            shown = self.cli("sidecar", "show", "Library")
            self.assertIn("Update state: Pending", shown.stdout)
            self.assertIn("Status: Embedding pending", shown.stdout)
            pending = json.loads(self.cli("search", "--sidecar", "Library", "astronomy").stdout)
            pdf = next(hit for hit in pending if hit["source"]["relative_path"] == "guide.pdf")
            self.assertEqual(pdf["freshness"], "current")
            self.assertEqual(pdf["index_freshness"], "stale")
            self.assertEqual(pdf["processing"]["state"], "pending")
            self.assertEqual(self.cli("retrieve", "--sidecar", "Library", old["evidence_id"]).returncode, 2)
        finally:
            self.server.state["resume"].set()
            stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("Changes: 1 added, 1 changed, 1 deleted, 1 unchanged", stdout)
        self.assertIn("Embeddings: Complete (3/3)", stdout)
        updated = self.cli("search", "--sidecar", "Library", "automobile")
        self.assertEqual(updated.returncode, 0, updated.stderr)
        hits = json.loads(updated.stdout)
        self.assertEqual(hits[0]["source"]["relative_path"], "cars.md")
        self.assertEqual(hits[0]["search"]["mode"], "hybrid")
        self.assertEqual(hits[0]["index_freshness"], "stale")
        self.assertEqual(hits[0]["processing"]["state"], "attention")
        self.assertIn("Removed source: orbit.py", self.cli("sidecar", "show", "Library").stdout)
        self.assertNotIn(SECRET, stdout + stderr + shown.stdout + updated.stdout + updated.stderr)

    def test_watch_catch_up_reuses_unchanged_vectors_and_exposes_pending_work(self) -> None:
        self.configure()
        self.assertEqual(self.cli("sidecar", "index", "Library").returncode, 0)
        self.assertEqual(self.cli("sidecar", "index", "Private").returncode, 0)
        initial_requests = len(self.server.state["requests"])
        self.assertEqual(initial_requests, 2)
        unchanged = self.cli("watch", "--once")
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertEqual(len(self.server.state["requests"]), initial_requests)
        self.assertIn("status Indexed", unchanged.stdout)

        (self.source / "cars.md").write_text("An automobile engine is documented here.\n")
        self.server.state["pause_on_request"] = initial_requests + 1
        env = os.environ.copy()
        env["PSYDCAR_HOME"] = str(self.store)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["PSYDCAR_TEST_KEY"] = SECRET
        process = subprocess.Popen(
            [sys.executable, "-m", "psydcar", "watch", "--once"],
            cwd=self.work_two, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertTrue(self.server.state["pause_entered"].wait(5), "watch catch-up did not reach the gateway")
            shown = self.cli("sidecar", "show", "Library")
            self.assertIn("Update state: Pending", shown.stdout)
            search = self.cli("search", "--sidecar", "Library", "astronomy")
            self.assertEqual(search.returncode, 0, search.stderr)
            pdf = next(hit for hit in json.loads(search.stdout) if hit["source"]["relative_path"] == "guide.pdf")
            self.assertEqual(pdf["freshness"], "current")
            self.assertEqual(pdf["index_freshness"], "stale")
            self.assertEqual(pdf["processing"]["state"], "pending")
        finally:
            self.server.state["resume"].set()
            stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("status Indexed", stdout)
        self.assertEqual(len(self.server.state["requests"]), initial_requests + 1)
        self.assertIn("Update state: Current", self.cli("sidecar", "show", "Library").stdout)
        self.assertNotIn(SECRET, unchanged.stdout + unchanged.stderr + shown.stdout + search.stdout + search.stderr + stdout + stderr)

    def test_validation_and_missing_credential(self) -> None:
        invalid = self.cli("provider", "set", "--base-url", "http://example.com/v1", "--model", "test")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("--allow-insecure-http", invalid.stderr)
        embedded_secret = self.cli("provider", "set", "--base-url", "https://key@example.com/v1", "--model", "test")
        self.assertEqual(embedded_secret.returncode, 2)
        self.assertIn("without credentials", embedded_secret.stderr)
        self.assertEqual(self.cli("provider", "show").stdout.strip(), "Embedding provider: Not configured")

        self.configure()
        missing = self.cli("sidecar", "index", "Library", credential=False)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("PSYDCAR_TEST_KEY", missing.stderr)
        self.assertNotIn(SECRET, missing.stdout + missing.stderr)
        self.assertEqual(self.server.state["requests"], [])
        self.assertIn("Embeddings: Error (0/3)", self.cli("sidecar", "show", "Library").stdout)


if __name__ == "__main__":
    unittest.main()
