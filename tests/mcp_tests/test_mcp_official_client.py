"""Verify the zero-dependency MCP server with the official MCP Python client.

Skipped when the optional `mcp` package is not installed. The server itself does not depend on it;
this test only proves third-party compatibility.
"""
import asyncio
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.catalog import labels as label_import
from tests.labels_tests.test_import_labels import CREATED, make_db

HAS_MCP = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(HAS_MCP, "official mcp client not installed")
class OfficialMcpClientTest(unittest.TestCase):
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

    def test_official_client_initialize_list_and_call(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def scenario():
            params = StdioServerParameters(command=".venv/bin/python",
                                           args=["-m", "backend.mcp_server", "--database", str(self.db)],
                                           cwd=str(Path(__file__).resolve().parents[2]))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool("count_papers",
                                                     {"filters": {"area_ids": ["ml_foundations"]}})
                    return init, tools, result

        init, tools, result = asyncio.run(scenario())
        self.assertEqual(init.server_info.name, "aipaperbase")
        self.assertEqual(len(tools.tools), 11)
        self.assertFalse(result.is_error)
        self.assertIn('"total": 1', result.content[0].text)


if __name__ == "__main__":
    unittest.main()
