from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from psydcar.freshness import SidecarWatcher
from psydcar.ocr import _TABLES
from psydcar.registry import SidecarStore
from psydcar.storage import connect
from psydcar.vision import VisionProviderStore
from test_mcp import MCPProcess
from test_pdf_cli import write_pdf

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VisionGateway(ThreadingHTTPServer):
    requests: list[dict[str, Any]]


class VisionHandler(BaseHTTPRequestHandler):
    server: VisionGateway  # ty: ignore[incompatible-mutable-override]

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append({"path": self.path, "body": body})
        payload = json.dumps({"choices": [{"message": {"content": "Copper lantern orchard"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class ImageOCRTests(unittest.TestCase):
    def test_turning_off_running_ocr_stops_after_current_request_and_keeps_results(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class PausedHandler(VisionHandler):
            def do_POST(self) -> None:
                started.set()
                release.wait(10)
                super().do_POST()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, store = root / "source", root / "store"
            source.mkdir()
            Image.new("RGB", (20, 20), "white").save(source / "a.png")
            Image.new("RGB", (20, 20), "black").save(source / "b.png")
            gateway = VisionGateway(("127.0.0.1", 0), PausedHandler)
            gateway.requests = []
            thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(gateway.server_close)
            self.addCleanup(gateway.shutdown)
            self.addCleanup(release.set)
            environment = os.environ.copy()
            environment.update(PSYDCAR_HOME=str(store), PYTHONPATH=str(PROJECT_ROOT / "src"))

            def cli(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run([sys.executable, "-m", "psydcar", *args], env=environment, text=True, capture_output=True)

            self.assertEqual(cli("sidecar", "create", "Library", str(source)).returncode, 0)
            self.assertEqual(
                cli("ocr-provider", "set", "--base-url", f"http://127.0.0.1:{gateway.server_port}/v1", "--model", "vision-test").returncode, 0
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "psydcar", "sidecar", "ocr", "Library"],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdin is not None
            process.stdin.write("yes\n")
            process.stdin.flush()
            self.assertTrue(started.wait(10))
            stopped = cli("sidecar", "ocr", "Library", "--off")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertIn("already in progress may finish", stopped.stdout)
            release.set()
            process.communicate(timeout=10)
            self.assertEqual(len(gateway.requests), 1)
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            results = json.loads(cli("search", "--sidecar", "Library", "lantern").stdout)
            self.assertEqual((len(gateway.requests), results[0]["source"]["relative_path"]), (1, "a.png"))

    def test_confirmed_image_is_searchable_and_retrievable_through_cli_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            store = root / "store"
            source = root / "source"
            source.mkdir()
            (source / "sign.png").write_bytes(
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
                )
            )
            (source / "notes.txt").write_text("Existing text remains searchable.\n")
            try:
                server = VisionGateway(("127.0.0.1", 0), VisionHandler)
            except PermissionError:
                self.skipTest("loopback sockets are unavailable in this sandbox")
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            environment = os.environ.copy()
            environment.update(PSYDCAR_HOME=str(store), PYTHONPATH=str(PROJECT_ROOT / "src"))

            def cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
                return subprocess.run([sys.executable, "-m", "psydcar", *args], env=environment, input=input_text, text=True, capture_output=True)

            self.assertEqual(cli("sidecar", "create", "Library", str(source)).returncode, 0)
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            self.assertEqual(cli("ocr-provider", "set", "--base-url", endpoint, "--model", "vision-test").returncode, 0)
            self.assertEqual(server.requests, [])
            enabled = cli("sidecar", "ocr", "Library", input_text="yes\n")
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.assertIn("1 supported image", enabled.stdout)
            self.assertIn(endpoint, enabled.stdout)
            self.assertEqual(server.requests[0]["path"], "/v1/chat/completions")
            self.assertTrue(server.requests[0]["body"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
            results = json.loads(cli("search", "--sidecar", "Library", "lantern").stdout)
            self.assertEqual(
                (results[0]["source"]["relative_path"], results[0]["extraction"], results[0]["freshness"]), ("sign.png", "ocr", "current")
            )
            retrieved = json.loads(cli("retrieve", "--sidecar", "Library", results[0]["evidence_id"]).stdout)
            self.assertEqual((retrieved["context"], retrieved["context_origin"]), ("Copper lantern orchard", "saved_ocr"))
            process = MCPProcess(store, root, results[0]["sidecar"]["id"])
            self.addCleanup(process.close)
            process.initialize()
            found = process.call("search", {"query": "lantern"})["structuredContent"]["results"][0]
            self.assertEqual(process.call("retrieve", {"evidence_id": found["evidence_id"]})["structuredContent"]["extraction"], "ocr")
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            self.assertEqual(len(server.requests), 1)
            (source / "new.png").write_bytes((source / "sign.png").read_bytes())
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            self.assertEqual(len(server.requests), 2)
            watcher = SidecarWatcher(SidecarStore(store / "state.sqlite3"), interval=0.05, reconcile_interval=10)
            watcher.step()
            self.assertEqual(len(server.requests), 2)
            (source / "new.png").write_bytes((source / "sign.png").read_bytes() + b"changed")
            self.assertEqual(watcher.step()[0].reason, "source_changed")
            self.assertEqual(len(server.requests), 3)
            self.assertIn("OCR: Complete (2/2 images and PDF pages; 0 need attention)", cli("sidecar", "show", "Library").stdout)
            (store / "search.sqlite3").unlink()
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            self.assertEqual(json.loads(cli("search", "--sidecar", "Library", "lantern").stdout)[0]["context"], "Copper lantern orchard")
            self.assertEqual(len(server.requests), 3)

    def test_confirmed_pdf_ocr_preserves_text_and_reuses_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            store = root / "store"
            source = root / "source"
            source.mkdir()
            text_pdf = root / "text.pdf"
            write_pdf(text_pdf, ["Searchable solar text"])
            scanned = Image.new("RGB", (500, 300), "white")
            ImageDraw.Draw(scanned).text((30, 80), "Copper lantern orchard", fill="black")
            image_pdf = BytesIO()
            scanned.save(image_pdf, format="PDF")
            writer = PdfWriter()
            writer.add_page(PdfReader(text_pdf).pages[0])
            writer.pages[0].merge_page(PdfReader(BytesIO(image_pdf.getvalue())).pages[0], over=False)
            writer.add_page(PdfReader(BytesIO(image_pdf.getvalue())).pages[0])
            with (source / "mixed.pdf").open("wb") as output:
                writer.write(output)

            server = VisionGateway(("127.0.0.1", 0), VisionHandler)
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            environment = os.environ.copy()
            environment.update(PSYDCAR_HOME=str(store), PYTHONPATH=str(PROJECT_ROOT / "src"))

            def cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
                return subprocess.run([sys.executable, "-m", "psydcar", *args], env=environment, input=input_text, text=True, capture_output=True)

            self.assertEqual(cli("sidecar", "create", "Library", str(source)).returncode, 0)
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            self.assertEqual(cli("ocr-provider", "set", "--base-url", endpoint, "--model", "vision-test").returncode, 0)
            state = SidecarStore(store / "state.sqlite3")
            sidecar = state.get("Library")
            with connect(state.db_path, _TABLES) as connection:
                provider = VisionProviderStore(state).get()
                assert provider is not None
                connection.execute(
                    "INSERT INTO sidecar_ocr (sidecar_id, enabled_at, provider_revision, grant_token) VALUES (?, ?, ?, ?)",
                    (sidecar.id, "2026-01-01", provider.revision, "image-only-grant"),
                )
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            self.assertEqual(len(server.requests), 0)
            enabled = cli("sidecar", "ocr", "Library", input_text="yes\n")
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.assertIn("0 supported image", enabled.stdout)
            self.assertIn("PDF pages without searchable text: 1", enabled.stdout)
            self.assertEqual(len(server.requests), 1)
            self.assertTrue(server.requests[0]["body"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
            hit = json.loads(cli("search", "--sidecar", "Library", "lantern").stdout)[0]
            self.assertEqual((hit["source"]["relative_path"], hit["location"], hit["extraction"]), ("mixed.pdf", {"kind": "page", "page": 2}, "ocr"))
            retrieved = json.loads(cli("retrieve", "--sidecar", "Library", hit["evidence_id"]).stdout)
            self.assertEqual((retrieved["context"], retrieved["context_origin"]), ("Copper lantern orchard", "saved_ocr"))
            process = MCPProcess(store, root, sidecar.id)
            self.addCleanup(process.close)
            process.initialize()
            mcp_hit = process.call("search", {"query": "lantern"})["structuredContent"]["results"][0]
            mcp_page = process.call("retrieve", {"evidence_id": mcp_hit["evidence_id"]})["structuredContent"]
            self.assertEqual((mcp_page["location"]["page"], mcp_page["extraction"]), (2, "ocr"))
            self.assertEqual(json.loads(cli("search", "--sidecar", "Library", "solar").stdout)[0]["location"]["page"], 1)
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            self.assertEqual(len(server.requests), 1)
            expanded = cli("sidecar", "ocr", "Library", "--full-pdf", input_text="yes\n")
            self.assertEqual(expanded.returncode, 0, expanded.stderr)
            self.assertIn("2 total pages, including 1 additional page with searchable text", expanded.stdout)
            self.assertEqual(len(server.requests), 2)
            reviewed = cli("sidecar", "ocr", "Library", input_text="yes\n")
            self.assertIn("2 total pages, including 1 additional page with searchable text", reviewed.stdout)
            hits = json.loads(cli("search", "--sidecar", "Library", "lantern").stdout)
            full_hit = next(hit for hit in hits if hit["location"]["page"] == 1)
            full_result = json.loads(cli("retrieve", "--sidecar", "Library", full_hit["evidence_id"]).stdout)
            self.assertEqual((full_result["extraction"], full_result["context_origin"]), ("ocr", "saved_ocr"))
            self.assertEqual(json.loads(cli("search", "--sidecar", "Library", "solar").stdout)[0]["location"]["page"], 1)
            self.assertIn("PDF OCR coverage: All pages", cli("sidecar", "show", "Library").stdout)
            (store / "search.sqlite3").unlink()
            self.assertEqual(cli("sidecar", "update", "Library").returncode, 0)
            self.assertEqual(len(json.loads(cli("search", "--sidecar", "Library", "lantern").stdout)), 2)
            self.assertEqual(len(server.requests), 2)
            watcher = SidecarWatcher(state, interval=0.05, reconcile_interval=10)
            watcher.step()
            (source / "new-scan.pdf").write_bytes(image_pdf.getvalue())
            self.assertEqual(watcher.step()[0].reason, "source_changed")
            self.assertEqual(len(server.requests), 3)
            mixed = source / "mixed.pdf"
            mixed.write_bytes(mixed.read_bytes() + b"\n")
            self.assertEqual(watcher.step()[0].reason, "source_changed")
            self.assertEqual(len(server.requests), 5)
