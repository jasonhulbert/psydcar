from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from psydcar.dashboard import DashboardServer
from psydcar.store import SidecarStore


PROBE_SECRET = "DASHBOARD_TEST_CREDENTIAL_DO_NOT_DISPLAY"


class ProviderProbeHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        state = self.server.state
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["requests"].append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
        if state["status"] != 200:
            self.send_response(state["status"])
            self.end_headers()
            return
        response = json.dumps({"data": [{"index": 0, "embedding": [1.0, 0.0]}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.work = self.root / "other-project"
        self.work.mkdir()
        self.database = self.root / "store" / "psydcar.sqlite3"
        try:
            self.server = DashboardServer(SidecarStore(self.database), port=0, watch_interval=0.1)
        except PermissionError as exc:
            self.skipTest(f"Local loopback HTTP is unavailable: {exc}")
        self.server.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.token = self.http("GET", "/api/bootstrap")[1]["token"]

    def close_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.stop()

    def http(self, method: str, path: str, body: dict | None = None, *, token: str | None = None,
             origin: str | None = None, host: str | None = None) -> tuple[int, dict]:
        headers = {"Accept": "application/json", "Host": host or f"127.0.0.1:{self.server.server_port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["X-Psydcar-Token"] = self.token if token is None else token
            headers["Origin"] = self.server.url if origin is None else origin
        request = Request(self.server.url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        environment["PSYDCAR_HOME"] = str(self.database.parent)
        return subprocess.run([sys.executable, "-m", "psydcar", *args], cwd=self.work,
                              env=environment, capture_output=True, text=True, check=False)

    def wait_for(self, predicate, description: str) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail(description)

    def entry(self, sidecar_id: str) -> dict:
        sidecars = self.http("GET", "/api/sidecars")[1]["sidecars"]
        return next(item for item in sidecars if item["id"] == sidecar_id)

    def test_browser_management_shares_cli_state_and_watches_sources(self) -> None:
        self.assertEqual(self.http("GET", "/api/bootstrap")[1]["sidecars"], [])
        (self.source / "notes.md").write_text("quiet orchard\n")
        status, created = self.http("POST", "/api/sidecars", {"name": "Research", "source": str(self.source)})
        self.assertEqual(status, 201)
        sidecar_id = created["id"]
        self.wait_for(lambda: self.entry(sidecar_id)["coverage"]["passages"] == 1
                      and self.entry(sidecar_id)["state"] == "current", "browser create did not index")
        entry = self.entry(sidecar_id)
        self.assertEqual(entry["coverage"]["files"], 1)
        self.assertEqual(entry["state"], "current")
        self.assertTrue(self.http("GET", "/api/sidecars")[1]["watching"])
        shown = self.cli("sidecar", "show", sidecar_id)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Name: Research", shown.stdout)
        self.assertIn(f"ID: {sidecar_id}", shown.stdout)
        self.assertIn("Status: Indexed", shown.stdout)

        renamed = self.cli("sidecar", "rename", sidecar_id, "CLI rename")
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        self.assertEqual(self.entry(sidecar_id)["name"], "CLI rename")
        status, _ = self.http("POST", f"/api/sidecars/{sidecar_id}/rename", {"name": "Browser rename"})
        self.assertEqual(status, 200)
        self.assertIn("Name: Browser rename", self.cli("sidecar", "show", sidecar_id).stdout)

        (self.source / "notes.md").write_text("quiet orchard changed\n")
        self.wait_for(lambda: bool(self.cli("search", "--sidecar", sidecar_id, "changed").stdout.strip().startswith("["))
                      and len(json.loads(self.cli("search", "--sidecar", sidecar_id, "changed").stdout)) > 0,
                      "dashboard watcher did not refresh changed source")
        self.assertEqual(self.entry(sidecar_id)["state"], "current")
        (self.source / "notes.md").unlink()
        self.wait_for(lambda: self.entry(sidecar_id)["status"] == "source_attention", "source deletion was not visible")
        entry = self.entry(sidecar_id)
        self.assertEqual(entry["coverage"]["files"], 0)
        self.assertIn("Removed source: notes.md", entry["update"]["issues"])
        self.assertEqual(entry["state"], "attention")
        self.assertEqual(self.http("POST", f"/api/sidecars/{sidecar_id}/update", {})[0], 202)
        self.wait_for(lambda: self.entry(sidecar_id)["state"] == "current", "manual update did not clear attention")

        (self.source / "retained.md").write_text("source stays here\n")
        status, removed = self.http("DELETE", f"/api/sidecars/{sidecar_id}", {"confirm_id": sidecar_id})
        self.assertEqual(status, 200)
        self.assertIn("Source files were not changed", removed["message"])
        self.assertTrue((self.source / "retained.md").exists())
        self.assertEqual(self.http("GET", "/api/sidecars")[1]["sidecars"], [])
        self.assertIn("No sidecars yet", self.cli("sidecar", "list").stdout)

    def test_invalid_input_and_local_request_guards(self) -> None:
        status, payload = self.http("POST", "/api/sidecars", {"name": "Bad", "source": str(self.root / "missing")})
        self.assertEqual(status, 400)
        self.assertIn("does not exist", payload["error"])
        status, payload = self.http("POST", "/api/sidecars", {"name": " ", "source": str(self.source)})
        self.assertEqual(status, 400)
        self.assertIn("nonempty", payload["error"])
        self.assertEqual(self.http("POST", "/api/sidecars", {"name": "Bad", "source": str(self.source)}, token="wrong")[0], 403)
        self.assertEqual(self.http("POST", "/api/sidecars", {"name": "Bad", "source": str(self.source)}, origin="http://evil.example")[0], 403)
        self.assertEqual(self.http("GET", "/api/sidecars", host="evil.example")[0], 403)
        status, created = self.http("POST", "/api/sidecars", {"name": "Okay", "source": str(self.source)})
        self.assertEqual(status, 201)
        self.assertEqual(self.http("POST", "/api/sidecars", {"name": "Okay", "source": str(self.source)})[0], 400)
        self.assertEqual(self.http("DELETE", f"/api/sidecars/{created['id']}", {"confirm_id": "wrong"})[0], 400)
        self.assertEqual(len(self.http("GET", "/api/sidecars")[1]["sidecars"]), 1)

    def test_failed_source_keeps_coverage_and_exposes_recovery(self) -> None:
        (self.source / "keep.md").write_text("still searchable\n")
        sidecar_id = self.http("POST", "/api/sidecars", {"name": "Fragile", "source": str(self.source)})[1]["id"]
        self.wait_for(lambda: self.entry(sidecar_id)["coverage"]["passages"] == 1
                      and self.entry(sidecar_id)["state"] == "current", "initial update did not complete")
        moved = self.root / "temporarily-moved"
        self.source.rename(moved)
        self.wait_for(lambda: self.entry(sidecar_id)["status"] == "index_error", "missing source was not reported")
        entry = self.entry(sidecar_id)
        self.assertEqual(entry["state"], "attention")
        self.assertEqual(entry["freshness"], "stale")
        self.assertEqual(entry["coverage"]["passages"], 1)
        self.assertTrue(any("Source directory" in issue for issue in entry["update"]["issues"]))
        self.source = moved.rename(self.source)
        self.assertEqual(self.http("POST", f"/api/sidecars/{sidecar_id}/update", {})[0], 202)
        self.wait_for(lambda: self.entry(sidecar_id)["state"] == "current", "recovery was not shown")
        self.assertEqual(self.entry(sidecar_id)["update"]["issues"], [])

    def test_provider_setup_tests_only_sample_text_and_hides_credential(self) -> None:
        source_secret = "PRIVATE_SOURCE_TEXT_MUST_NOT_REACH_PROVIDER_TEST"
        (self.source / "private.md").write_text(source_secret)
        sidecar = self.server.store.create("Private notes", self.source)
        self.wait_for(lambda: self.entry(sidecar.id)["coverage"]["passages"] == 1,
                      "private source was not indexed before provider test")
        try:
            gateway = ThreadingHTTPServer(("127.0.0.1", 0), ProviderProbeHandler)
        except PermissionError as exc:
            self.skipTest(f"Local gateway is unavailable: {exc}")
        gateway.state = {"requests": [], "status": 200}
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        self.addCleanup(gateway_thread.join, 5)
        self.addCleanup(gateway.server_close)
        self.addCleanup(gateway.shutdown)
        draft = {"base_url": f"http://127.0.0.1:{gateway.server_port}/v1", "model": "demo-model",
                 "api_key_env": "PSYDCAR_DASHBOARD_TEST_KEY", "allow_insecure_http": False}
        with patch.dict(os.environ, {"PSYDCAR_DASHBOARD_TEST_KEY": PROBE_SECRET}):
            self.assertEqual(self.http("GET", "/api/bootstrap")[1]["provider"]["state"], "unconfigured")
            status, tested = self.http("POST", "/api/provider/test", draft)
            self.assertEqual(status, 200, tested)
            self.assertEqual(tested["dimensions"], 2)
            self.assertEqual(gateway.state["requests"], [{
                "path": "/v1/embeddings", "body": {"model": "demo-model", "input": ["Psydcar provider connection test."]},
                "authorization": f"Bearer {PROBE_SECRET}",
            }])
            self.assertNotIn(source_secret, json.dumps(gateway.state["requests"]))
            self.assertIsNone(self.server.provider.get())
            status, saved = self.http("POST", "/api/provider", draft)
            self.assertEqual(status, 200, saved)
            self.assertEqual(saved["provider"]["state"], "ready")
            self.assertEqual(saved["provider"]["api_key_env"], "PSYDCAR_DASHBOARD_TEST_KEY")
            self.assertNotIn(PROBE_SECRET, json.dumps(saved) + json.dumps(self.http("GET", "/api/bootstrap")[1]))
            self.assertIn("Model: demo-model", self.cli("provider", "show").stdout)
            if importlib.util.find_spec("mcp"):
                status, preview = self.http("POST", "/api/connections/preview",
                                            {"name": "private_project", "sidecar_ids": [sidecar.id]})
                self.assertEqual(status, 200, preview)
                copied = json.dumps(preview["config"])
                self.assertNotIn(PROBE_SECRET, copied)
                self.assertNotIn("PSYDCAR_DASHBOARD_TEST_KEY", copied)

            invalid = dict(draft, base_url="http://user:pass@example.com/v1")
            self.assertEqual(self.http("POST", "/api/provider", invalid)[0], 400)
            self.assertEqual(self.server.provider.get().base_url, draft["base_url"])
            invalid = dict(draft, base_url="http://example.com/v1")
            status, invalid_response = self.http("POST", "/api/provider", invalid)
            self.assertEqual(status, 400)
            self.assertIn("Allow remote HTTP", invalid_response["error"])
            self.assertEqual(self.http("POST", "/api/provider", dict(draft, api_key_env=123))[0], 400)

            gateway.state["status"] = 503
            status, failed = self.http("POST", "/api/provider/test", draft)
            self.assertEqual(status, 502)
            self.assertIn("unavailable", failed["hint"])
            self.assertNotIn(PROBE_SECRET, json.dumps(failed))
            os.environ.pop("PSYDCAR_DASHBOARD_TEST_KEY")
            self.assertEqual(self.http("GET", "/api/sidecars")[1]["provider"]["state"], "missing_credential")
            status, missing = self.http("POST", "/api/provider/test", draft)
            self.assertEqual(status, 502)
            self.assertIn("Set that environment variable", missing["hint"])
            self.assertNotIn(PROBE_SECRET, json.dumps(missing))

            self.assertEqual(self.http("DELETE", "/api/provider", {"confirm": "wrong"})[0], 400)
            self.assertEqual(self.http("DELETE", "/api/provider", {"confirm": "clear"})[0], 200)
            self.assertEqual(self.http("GET", "/api/sidecars")[1]["provider"]["state"], "unconfigured")

    def test_connection_preview_uses_exact_ids_and_local_store(self) -> None:
        one = self.server.store.create("One", self.source)
        second_source = self.root / "second"
        second_source.mkdir()
        two = self.server.store.create("Two", second_source)
        self.assertEqual(self.http("POST", "/api/connections/preview", {"name": "project_a", "sidecar_ids": []})[0], 400)
        self.assertEqual(self.http("POST", "/api/connections/preview", {"name": "bad name", "sidecar_ids": [one.id]})[0], 400)
        self.assertEqual(self.http("POST", "/api/connections/preview", {"name": "project_a", "sidecar_ids": ["One"]})[0], 400)
        self.assertEqual(self.http("POST", "/api/connections/preview", {"name": "project_a", "sidecar_ids": [one.id, "missing"]})[0], 400)
        status, preview = self.http("POST", "/api/connections/preview", {"name": "project_a", "sidecar_ids": [one.id, one.id]})
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["selected"], [{"id": one.id, "name": "One"}])
        entry = preview["config"]["mcpServers"]["project_a"]
        self.assertEqual(entry["command"], str(Path.home() / ".local" / "bin" / "psydcar"))
        self.assertEqual(entry["args"], ["mcp", "--sidecar-id", one.id])
        self.assertEqual(entry["env"], {"PSYDCAR_HOME": str(self.database.parent)})
        self.assertNotIn(two.id, json.dumps(preview))
        status, combined = self.http("POST", "/api/connections/preview",
                                     {"name": "project_b", "sidecar_ids": [one.id, two.id]})
        self.assertEqual(status, 200)
        self.assertEqual(combined["entry"]["args"],
                         ["mcp", "--sidecar-id", one.id, "--sidecar-id", two.id])

    def test_static_assets_and_package_data(self) -> None:
        for path in ("/", "/assets/dashboard.css", "/assets/dashboard.js"):
            request = Request(self.server.url + path)
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
                self.assertGreater(len(response.read()), 100)


if __name__ == "__main__":
    unittest.main()
