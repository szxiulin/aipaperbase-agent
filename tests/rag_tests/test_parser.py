from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.library import store as download_store
from backend.rag import loader, parse_store, parser


class FakeResult:
    def __init__(self, markdown="# 标题\n\n正文内容", error: str = ""):
        self.markdown = markdown
        self.error = error
        self.state = "done" if markdown is not None else "failed"

    def save_markdown(self, path, with_images=True):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.markdown, encoding="utf-8")
        return path


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.papers = Path(self.tmp) / "papers"
        self.papers.mkdir(parents=True, exist_ok=True)
        self.parsed = Path(self.tmp) / "parsed"

        self.download_conn = download_store.connect(self.papers / "downloads.sqlite")
        download_store.initialize(self.download_conn)
        self.papers_patch = mock.patch.object(download_store, "PAPERS_ROOT", self.papers)
        self.papers_patch.start()

        self.parse_conn = parse_store.connect(self.parsed / "parsed.sqlite")
        parse_store.initialize(self.parse_conn)
        self.parsed_patch = mock.patch.object(parse_store, "PARSED_ROOT", self.parsed)
        self.parsed_patch.start()

    def tearDown(self) -> None:
        self.parsed_patch.stop()
        self.papers_patch.stop()
        self.parse_conn.close()
        self.download_conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mark_downloaded(self, *entity_ids: str) -> None:
        for entity_id in entity_ids:
            path = download_store.paper_path(entity_id)
            path.write_bytes(b"%PDF-1.4 fake")
            download_store.mark(
                self.download_conn, entity_id, "success",
                source="arxiv", download_url="https://x",
                sha256="abc", file_path=str(path), bytes=100,
            )

    def test_parse_success(self) -> None:
        self._mark_downloaded("e1", "e2")
        with mock.patch.object(loader, "extract_batch", return_value=[FakeResult(), FakeResult("# 方法\n内容")]):
            results = parser.run_parse(self.download_conn, self.parse_conn, ["e1", "e2"])
        self.assertEqual(results["parsed"], 2)
        self.assertEqual(results["total"], 2)
        for entity_id in ("e1", "e2"):
            self.assertTrue(parse_store.parsed_path(entity_id).exists())
            self.assertTrue(parse_store.is_parsed(self.parse_conn, entity_id))

    def test_skip_already_parsed(self) -> None:
        self._mark_downloaded("e1")
        parse_store.mark(self.parse_conn, "e1", "success", source_pdf_path="x", markdown_path="y", char_count=1)
        parse_store.parsed_path("e1").parent.mkdir(parents=True, exist_ok=True)
        parse_store.parsed_path("e1").write_text("# 标题", encoding="utf-8")
        with mock.patch.object(loader, "extract_batch") as mocked:
            results = parser.run_parse(self.download_conn, self.parse_conn, ["e1"])
        self.assertEqual(results["skipped"], 1)
        mocked.assert_not_called()

    def test_no_pdf_is_skipped_without_call(self) -> None:
        with mock.patch.object(loader, "extract_batch") as mocked:
            results = parser.run_parse(self.download_conn, self.parse_conn, ["missing"])
        self.assertEqual(results["no_pdf"], 1)
        mocked.assert_not_called()

    def test_failure_recorded(self) -> None:
        self._mark_downloaded("e1")
        with mock.patch.object(loader, "extract_batch", return_value=[FakeResult(None, error="解析失败")]):
            results = parser.run_parse(self.download_conn, self.parse_conn, ["e1"])
        self.assertEqual(results["failed"], 1)
        row = self.parse_conn.execute(
            "SELECT status, error FROM parsed_documents WHERE entity_id = 'e1'"
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"])

    def test_batch_error_marks_all_failed(self) -> None:
        self._mark_downloaded("e1", "e2")
        with mock.patch.object(loader, "extract_batch", side_effect=RuntimeError("批量失败")):
            results = parser.run_parse(self.download_conn, self.parse_conn, ["e1", "e2"])
        self.assertEqual(results["failed"], 2)


if __name__ == "__main__":
    unittest.main()
