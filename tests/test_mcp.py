from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from psydcar.evidence import EvidenceStore
from psydcar.store import SidecarStore


class MCPProcess:
    def __init__(self, store_dir: Path, workdir: Path, *sidecar_ids: str, proposal_ids: tuple[str, ...] = ()):
        environment = os.environ.copy()
        environment["PSYDCAR_HOME"] = str(store_dir)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(PROJECT_ROOT / "src"), environment.get("PYTHONPATH"))))
        command = [sys.executable, "-m", "psydcar", "mcp"]
        for sidecar_id in sidecar_ids:
            command.extend(("--sidecar-id", sidecar_id))
        for sidecar_id in proposal_ids:
            command.extend(("--propose-sidecar-id", sidecar_id))
        if proposal_ids:
            command.extend(("--agent-name", "Review assistant"))
        self.process = subprocess.Popen(command, cwd=workdir, env=environment, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.lines: queue.Queue[str] = queue.Queue()
        assert self.process.stdout is not None
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 1

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                response = json.loads(self.lines.get(timeout=max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                break
            if response.get("id") == request_id:
                return response
        raise AssertionError(f"No MCP response for {method}; process exit: {self.process.poll()}")

    def send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def initialize(self) -> dict:
        response = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "psydcar-test", "version": "1"},
        })
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def call(self, name: str, arguments: dict) -> dict:
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        return response["result"]

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=5)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the declared mcp dependency for protocol tests")
class MCPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store_dir = self.root / "store"
        self.store = SidecarStore(self.store_dir / "psydcar.sqlite3")
        self.work_a = self.root / "project-a"
        self.work_b = self.root / "project-b"
        self.work_a.mkdir()
        self.work_b.mkdir()
        self.sources = [self.root / name for name in ("alpha", "beta", "private")]
        for source in self.sources:
            source.mkdir()
        for source, content in zip(self.sources, ("sharedterm alphaonly\n", "sharedterm betaonly\n", "sharedterm privateonly\n")):
            (source / "note.md").write_text(content)
        self.sidecars = [self.store.create(name, source) for name, source in zip(("Alpha", "Beta", "Private"), self.sources)]
        evidence = EvidenceStore(self.store)
        for sidecar in self.sidecars:
            evidence.update(sidecar.id)

    def process(self, workdir: Path, *ids: str, proposal_ids: tuple[str, ...] = ()) -> MCPProcess:
        process = MCPProcess(self.store_dir, workdir, *ids, proposal_ids=proposal_ids)
        self.addCleanup(process.close)
        return process

    def test_app_save_is_available_to_an_existing_connection(self) -> None:
        from fastapi.testclient import TestClient

        from psydcar.api import create_app

        sidecar = self.sidecars[0]
        connection = self.process(self.work_a, sidecar.id)
        connection.initialize()
        origin = "http://127.0.0.1:8765"
        with TestClient(create_app(self.store, 8765), base_url=origin) as app:
            token = app.get("/api/v1/session").json()["token"]
            url = f"/api/v1/sidecars/{sidecar.id}/file?path=note.md"
            opened = app.get(url).json()
            saved = app.put(url, json={"content": "# Lantern meadow\n", "version": opened["version"]},
                            headers={"Origin": origin, "X-Psydcar-Token": token})
            self.assertEqual(saved.status_code, 200, saved.text)
            results = []
            deadline = time.monotonic() + 8
            while not results and time.monotonic() < deadline:
                results = connection.call("search", {"query": "lantern"})["structuredContent"]["results"]
                if not results:
                    time.sleep(0.1)
            self.assertTrue(results, "The saved file was not reindexed")
            result = connection.call("retrieve", {"evidence_id": results[0]["evidence_id"]})["structuredContent"]
            self.assertEqual(result["context"], "# Lantern meadow")

    def test_review_edits_submitted_by_an_agent(self) -> None:
        from fastapi.testclient import TestClient

        from psydcar.api import create_app

        sidecar = self.sidecars[0]
        path = self.sources[0] / "note.md"
        original = b"\xef\xbb\xbf# Notes\r\n"
        path.write_bytes(original)
        connection = self.process(self.work_a, sidecar.id, proposal_ids=(sidecar.id,))
        connection.initialize()
        target = {"sidecar_id": sidecar.id, "path": "note.md"}
        base = connection.call("read_editable_file", target)["structuredContent"]

        def propose(text: str, summary: str) -> str:
            result = connection.call("propose_edit", {
                **target, "version": base["version"], "content": text, "summary": summary,
            })
            return result["structuredContent"]["id"]

        approved_id = propose("# Notes\nLantern meadow\n", "Add the latest field note.")
        rejected_id = propose("# Notes\nMove to archive.\n", "Suggest an archive note.")
        self.assertEqual(path.read_bytes(), original)

        origin = "http://127.0.0.1:8765"
        url = f"/api/v1/sidecars/{sidecar.id}/proposals"
        with TestClient(create_app(self.store, 8765), base_url=origin) as app:
            token = app.get("/api/v1/session").json()["token"]
            headers = {"Origin": origin, "X-Psydcar-Token": token}
            detail = app.get(f"{url}/{approved_id}").json()
            self.assertEqual(detail["agent_name"], "Review assistant")
            self.assertIn("+Lantern meadow", detail["diff"])
            for proposal_id, decision in ((rejected_id, "rejected"), (approved_id, "approved")):
                app.post(f"{url}/{proposal_id}", json={"decision": decision}, headers=headers).raise_for_status()
            history = app.get(url, params={"reviewed": "true"}).json()
            self.assertEqual({item["id"]: item["status"] for item in history["items"]},
                             {approved_id: "approved", rejected_id: "rejected"})
            self.assertEqual(path.read_bytes(), b"\xef\xbb\xbf# Notes\r\nLantern meadow\r\n")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                hits = connection.call("search", {"query": "lantern"})["structuredContent"]["results"]
                if hits:
                    break
                time.sleep(0.1)
            else:
                self.fail("The approved edit was not reindexed")

    def test_protocol_selection_discovery_structured_evidence_and_freshness(self) -> None:
        alpha, beta, private = self.sidecars
        connection = self.process(self.work_a, alpha.id, beta.id)
        initialized = connection.initialize()
        self.assertIn("result", initialized)
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "psydcar")
        tools = connection.request("tools/list")["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {"search", "retrieve"})
        for tool in tools:
            self.assertNotIn("sidecar", json.dumps(tool["inputSchema"]).lower())

        searched = connection.call("search", {"query": "sharedterm"})
        self.assertFalse(searched.get("isError"), searched)
        results = searched["structuredContent"]["results"]
        self.assertEqual({item["sidecar"]["id"] for item in results}, {alpha.id, beta.id})
        self.assertTrue(all(item["source"]["relative_path"] == "note.md" for item in results))
        self.assertTrue(all(item["location"] == {"kind": "lines", "start": 1, "end": 1} for item in results))
        self.assertTrue(all(item["freshness"] == "current" for item in results))
        self.assertEqual(connection.call("search", {"query": "privateonly"})["structuredContent"]["results"], [])
        injected = connection.call("search", {"query": "privateonly", "sidecar_ids": [private.id]})
        self.assertFalse(injected.get("isError"), injected)
        self.assertEqual(injected["structuredContent"]["results"], [])

        alpha_hit = next(item for item in results if item["sidecar"]["id"] == alpha.id)
        retrieved = connection.call("retrieve", {"evidence_id": alpha_hit["evidence_id"]})
        self.assertFalse(retrieved.get("isError"), retrieved)
        self.assertEqual(retrieved["structuredContent"]["evidence_id"], alpha_hit["evidence_id"])
        self.assertEqual(retrieved["structuredContent"]["context_origin"], "source")
        private_hit = EvidenceStore(self.store).search([private.id], "privateonly")[0]
        refused = connection.call("retrieve", {"evidence_id": private_hit["evidence_id"]})
        self.assertTrue(refused.get("isError"), refused)

        (self.sources[0] / "note.md").write_text("changed source\n")
        stale = connection.call("search", {"query": "alphaonly"})["structuredContent"]["results"][0]
        self.assertEqual(stale["freshness"], "changed")
        self.assertEqual(stale["index_freshness"], "stale")
        changed = connection.call("retrieve", {"evidence_id": alpha_hit["evidence_id"]})["structuredContent"]
        self.assertIsNone(changed["context"])
        self.assertEqual(changed["freshness"], "changed")
        (self.sources[0] / "note.md").unlink()
        missing = connection.call("retrieve", {"evidence_id": alpha_hit["evidence_id"]})["structuredContent"]
        self.assertIsNone(missing["context"])
        self.assertEqual(missing["freshness"], "missing")
        self.store.remove(beta.id)
        self.assertTrue(connection.call("search", {"query": "alphaonly"}).get("isError"))
        self.assertTrue(connection.call("retrieve", {"evidence_id": alpha_hit["evidence_id"]}).get("isError"))

    def test_no_selection_invalid_selection_and_reuse_from_two_projects(self) -> None:
        empty = self.process(self.work_a)
        self.assertEqual(empty.process.wait(timeout=5), 2)
        assert empty.process.stderr is not None
        self.assertIn("Select at least one sidecar ID", empty.process.stderr.read())
        self.assertEqual(empty.lines.qsize(), 0)

        named = self.process(self.work_a, "Alpha")
        self.assertEqual(named.process.wait(timeout=5), 2)
        assert named.process.stderr is not None
        self.assertIn("stable sidecar ID", named.process.stderr.read())
        unknown = self.process(self.work_a, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(unknown.process.wait(timeout=5), 2)
        mixed = self.process(self.work_a, self.sidecars[0].id, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(mixed.process.wait(timeout=5), 2)
        self.assertEqual(mixed.lines.qsize(), 0)

        first = self.process(self.work_a, self.sidecars[0].id)
        second = self.process(self.work_b, self.sidecars[0].id)
        self.assertIn("result", first.initialize())
        self.assertIn("result", second.initialize())
        for connection in (first, second):
            hits = connection.call("search", {"query": "alphaonly"})["structuredContent"]["results"]
            self.assertEqual({item["sidecar"]["id"] for item in hits}, {self.sidecars[0].id})


if __name__ == "__main__":
    unittest.main()
