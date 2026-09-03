from __future__ import annotations

import http.server
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from backend.library import downloader, store


def build_catalog_fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper_entities (entity_id TEXT PRIMARY KEY, canonical_record_id TEXT, canonical_title TEXT);
        CREATE TABLE paper_records (
            record_id TEXT PRIMARY KEY, authors TEXT, paper_url TEXT, doi TEXT,
            arxiv_id TEXT, pdf_url TEXT, venue TEXT, year INTEGER
        );
        CREATE TABLE entity_memberships (entity_id TEXT, record_id TEXT);
        """
    )
    records = [
        ("r1", "A", "https://a", "10.1/a", "2401.00001", "", "CVPR", 2024),
        ("r2", "B", "https://b", "10.1/b", "", "https://openaccess.thecvf.com/x.pdf", "CVPR", 2024),
        ("r3", "C", "https://c", "10.1/c", "", "https://openaccess.thecvf.com/y.pdf", "CVPR", 2024),
        ("r4", "D", "https://d", "10.1/d", "", "https://ieeexplore.ieee.org/z.pdf", "TPAMI", 2023),
    ]
    connection.executemany("INSERT INTO paper_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)
    connection.executemany("INSERT INTO paper_entities VALUES (?, ?, ?)", [
        ("e1", "r1", "One"), ("e2", "r2", "Two"), ("e3", "r3", "Three"), ("e4", "r4", "Four"),
    ])
    connection.executemany("INSERT INTO entity_memberships VALUES (?, ?)", [
        ("e1", "r1"), ("e2", "r2"), ("e3", "r3"), ("e4", "r4"),
    ])
    return connection


class DownloaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog_fixture()
        self.tmp = tempfile.mkdtemp()
        self.papers = Path(self.tmp) / "papers"
        self.store_conn = store.connect(self.papers / "downloads.sqlite")
        store.initialize(self.store_conn)
        self.papers_patch = mock.patch.object(store, "PAPERS_ROOT", self.papers)
        self.papers_patch.start()

    def tearDown(self) -> None:
        self.papers_patch.stop()
        self.store_conn.close()
        self.catalog.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _fake_download(content_map: dict | None = None, fail_urls: tuple = ()):
        content_map = content_map or {}
        def fake(url: str, *, timeout: int = 90, retries: int = 2, delay: float = 0.2):
            if url in fail_urls:
                raise RuntimeError("模拟下载失败")
            content = content_map.get(url) or (b"%PDF-1.4\n" + url.encode())
            return content, len(content)
        return fake

    def test_download_success_and_resume(self) -> None:
        fake = self._fake_download()
        with mock.patch.object(downloader, "download_pdf", side_effect=fake):
            results = downloader.run_download(self.catalog, self.store_conn, ["e1", "e2", "e3", "e4"])
        self.assertEqual(results["total"], 3)  # e4 is a restricted source, not counted
        self.assertEqual(results["success"], 3)
        for entity_id in ("e1", "e2", "e3"):
            path = store.paper_path(entity_id)
            self.assertTrue(path.exists())
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
        with mock.patch.object(downloader, "download_pdf", side_effect=fake):
            results2 = downloader.run_download(self.catalog, self.store_conn, ["e1", "e2", "e3", "e4"])
        self.assertEqual(results2["skipped"], 3)
        self.assertEqual(results2["success"], 0)

    def test_dedup_by_content(self) -> None:
        same = b"%PDF-1.4\n" + b"same content"
        content_map = {
            "https://openaccess.thecvf.com/x.pdf": same,
            "https://openaccess.thecvf.com/y.pdf": same,
        }
        with mock.patch.object(downloader, "download_pdf", side_effect=self._fake_download(content_map)):
            results = downloader.run_download(self.catalog, self.store_conn, ["e2", "e3"])
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["duplicate"], 1)
        counts = store.status_counts(self.store_conn)
        self.assertEqual(counts.get("success", 0), 1)
        self.assertEqual(counts.get("duplicate", 0), 1)

    def test_failure_is_recorded(self) -> None:
        fake = self._fake_download({}, fail_urls=("https://openaccess.thecvf.com/y.pdf",))
        with mock.patch.object(downloader, "download_pdf", side_effect=fake):
            results = downloader.run_download(self.catalog, self.store_conn, ["e2", "e3"])
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failed"], 1)
        row = self.store_conn.execute(
            "SELECT status, error FROM downloads WHERE entity_id = 'e3'"
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"])

    def test_status_map_and_remove(self) -> None:
        fake = self._fake_download()
        with mock.patch.object(downloader, "download_pdf", side_effect=fake):
            downloader.run_download(self.catalog, self.store_conn, ["e1", "e2"])
        statuses = store.status_map(self.store_conn, ["e1", "e2", "e3"])
        self.assertEqual(statuses["e1"], "success")
        self.assertEqual(statuses["e2"], "success")
        self.assertNotIn("e3", statuses)

        self.assertTrue(store.paper_path("e1").exists())
        result = store.remove(self.store_conn, "e1")
        self.assertTrue(result["removed"])
        self.assertFalse(store.paper_path("e1").exists())
        self.assertNotIn("e1", store.status_map(self.store_conn, ["e1"]))
        self.assertFalse(store.remove(self.store_conn, "nope")["removed"])


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ok.pdf":
            body = b"%PDF-1.4\n" + b"x" * 2048
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/bad.pdf":
            body = b"<html>not a pdf</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:
        pass


class DownloadPdfHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_valid_pdf(self) -> None:
        content, size = downloader.download_pdf(f"http://127.0.0.1:{self.port}/ok.pdf", delay=0)
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreater(size, 1024)

    def test_invalid_pdf(self) -> None:
        with self.assertRaises(RuntimeError):
            downloader.download_pdf(f"http://127.0.0.1:{self.port}/bad.pdf", retries=0, delay=0)

    def test_not_found(self) -> None:
        with self.assertRaises(RuntimeError):
            downloader.download_pdf(f"http://127.0.0.1:{self.port}/missing.pdf", retries=0, delay=0)


if __name__ == "__main__":
    unittest.main()
