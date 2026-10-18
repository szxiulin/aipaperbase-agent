import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.catalog import labels as label_import
from tests.labels_tests.test_import_labels import CREATED, make_db

ROOT = Path(__file__).resolve().parents[2]


class McpStdioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        directory = Path(cls._tmp.name)
        con, a, d, n, g, m = make_db(directory)
        label_import.import_labels(con, "rel", CREATED, areas_labels=a, directions_labels=d,
                                   non_paper_labels=n, golden=g, metrics=m)
        cls.db = directory / "catalog.sqlite"
        with sqlite3.connect(cls.db) as target:
            con.backup(target)
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_server(self, messages):
        payload = "\n".join(json.dumps(m) for m in messages) + "\n"
        proc = subprocess.run([sys.executable, "-m", "backend.mcp_server", "--database", str(self.db)],
                              input=payload, capture_output=True, text=True, timeout=30, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]

    def test_initialize_list_and_call(self):
        responses = self.run_server([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "count_papers", "arguments": {"filters": {"area_ids": ["ml_foundations"]}}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        ])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-03-26")
        tools = responses[1]["result"]["tools"]
        self.assertEqual(len(tools), 11)
        self.assertEqual(tools[0]["inputSchema"]["type"], "object")
        call = responses[2]["result"]
        self.assertFalse(call["isError"])
        self.assertEqual(call["structuredContent"]["data"]["total"], 1)
        self.assertTrue(responses[3]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
