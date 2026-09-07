from __future__ import annotations

import unittest
import sqlite3

from backend.library import ingest
from backend.library import identity


class IngestContractTest(unittest.TestCase):
    def test_contract_uses_stage_schema_and_keeps_unavailable_item(self) -> None:
        items = [
            {"requested_entity_id": "a", "canonical_entity_id": "a", "title": "A", "downloadable": True},
            {"requested_entity_id": "b", "canonical_entity_id": "b", "title": "B", "downloadable": False,
             "reason_code": "no_download_source"},
            {"requested_entity_id": "c", "canonical_entity_id": "c", "title": "C", "downloadable": True},
        ]
        result = ingest.finalize(items, {"success": 1}, {"parsed": 1}, {
            "documents": 1, "ingested_chunks": 7, "indexed_ids": ["a"], "chunk_counts": {"a": 7},
        })
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(result["ingested_documents"], 1)
        self.assertEqual(result["ingested_chunks"], 7)
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(result["items"][0]["status"], "indexed_current")
        self.assertEqual(result["items"][1]["reason_code"], "no_download_source")

    def test_zero_index_never_promises_fulltext_qa(self) -> None:
        result = ingest.new_result([{"requested_entity_id": "a"}])
        self.assertIn("暂不能进行全文问答", ingest.completion_message(result))
        self.assertNotIn("已可进行全文问答", ingest.completion_message(result))

    def test_completion_message_keeps_per_paper_source_failure(self) -> None:
        result = ingest.finalize(
            [{"requested_entity_id": "a", "canonical_entity_id": "a", "title": "IAMAgent",
              "downloadable": False, "reason_code": "source_timeout", "message": "OpenAlex 查询超时，稍后可重试。"}],
            {}, {}, {},
        )
        message = ingest.completion_message(result)
        self.assertIn("IAMAgent", message)
        self.assertIn("OpenAlex 查询超时", message)

    def test_identity_match_rules_are_conservative(self) -> None:
        item = {"doi": "10.1/x", "arxiv_id": "2401.1v2", "title": "Exact Title", "authors": "Alice", "years": [2024]}
        candidates = [
            {"doi": "10.1/x", "title": "different", "pdf_url": "x"},
            {"arxiv_id": "2401.1", "title": "different", "pdf_url": "x"},
            {"title": "Exact Title", "authors": ["Alice"], "year": 2020, "pdf_url": "x"},
        ]
        self.assertEqual(len(identity.candidate_matches(item, candidates)), 3)
        self.assertEqual(identity.candidate_matches({"title": "Exact Title", "authors": "", "years": []}, candidates), [])

    def test_duplicate_pdf_is_not_sent_to_parse_or_vector(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE downloads (entity_id TEXT, status TEXT, duplicate_of TEXT)")
        conn.executemany("INSERT INTO downloads VALUES (?, ?, ?)", [("a", "success", ""), ("b", "duplicate", "a")])
        items = [{"canonical_entity_id": "a", "downloadable": True}, {"canonical_entity_id": "b", "downloadable": True}]
        self.assertEqual(ingest.parseable_ids_after_pdf_dedup(conn, items), ["a"])
        self.assertEqual(items[1]["reason_code"], "duplicate_entity")
        conn.close()
