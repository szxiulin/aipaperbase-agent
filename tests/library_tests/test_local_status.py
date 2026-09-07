from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.agent.tools.local_library import local_library_tools
from backend.library import local_status


class LocalStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.catalog = sqlite3.connect(":memory:")
        self.catalog.row_factory = sqlite3.Row
        self.catalog.executescript("CREATE TABLE paper_entities (entity_id TEXT PRIMARY KEY, canonical_title TEXT);")
        self.catalog.executemany("INSERT INTO paper_entities VALUES (?, ?)", [("tir", "TIR-Agent"), ("iam", "IAMAgent")])
        self.downloads = sqlite3.connect(":memory:")
        self.downloads.row_factory = sqlite3.Row
        self.downloads.executescript("CREATE TABLE downloads (entity_id TEXT, status TEXT, source TEXT, updated_at TEXT, error TEXT);")
        self.downloads.execute("INSERT INTO downloads VALUES ('tir', 'success', 'openalex', '2026-09-04T00:00:00+00:00', '')")
        self.parsed = sqlite3.connect(":memory:")
        self.parsed.row_factory = sqlite3.Row
        self.parsed.executescript("""CREATE TABLE parsed_documents (
            entity_id TEXT, status TEXT, markdown_path TEXT, indexed_chunk_count INTEGER, parsed_sha256 TEXT,
            indexed_parsed_sha256 TEXT, indexed_pipeline_fingerprint TEXT, updated_at TEXT, error TEXT);""")
        self.path = Path(self.tmp) / "tir.md"
        self.path.write_text("# TIR\n\nbody", encoding="utf-8")
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.parsed.execute("INSERT INTO parsed_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           ("tir", "success", str(self.path), 42, self.digest, self.digest, "fingerprint", "2026-09-04T00:00:01+00:00", ""))

    def tearDown(self):
        self.catalog.close(); self.downloads.close(); self.parsed.close(); shutil.rmtree(self.tmp)

    def status(self, counts={"tir": 42}, fingerprint="fingerprint"):
        return local_status.snapshot(self.catalog, downloads_conn=self.downloads, parsed_conn=self.parsed,
                                     document_chunk_counts=counts, pipeline_fingerprint=fingerprint)

    def test_current_index_requires_actual_file_hash_fingerprint_and_chunk_count(self):
        result = self.status()
        self.assertEqual(result['summary']['indexed_current'], 1)
        self.assertEqual(result['summary']['chunks'], 42)
        self.assertEqual(result['items'][0]['index_status'], 'indexed_current')

    def test_hash_fingerprint_and_count_drift_are_stale(self):
        self.path.write_text("changed", encoding="utf-8")
        self.assertEqual(self.status()['items'][0]['index_status'], 'index_stale')
        self.path.write_text("# TIR\n\nbody", encoding="utf-8")
        self.assertEqual(self.status(fingerprint="new")['items'][0]['index_status'], 'index_stale')
        self.assertEqual(self.status(counts={"tir": 41})['items'][0]['index_status'], 'index_stale')

    def test_reparsed_but_not_successfully_indexed_is_stale(self):
        self.parsed.execute("UPDATE parsed_documents SET parsed_sha256=? WHERE entity_id='tir'", ("new-parse",))
        self.assertEqual(self.status()['items'][0]['index_status'], 'index_stale')

    def test_download_registration_is_not_claimed_as_a_present_pdf_file(self):
        result = self.status()
        self.assertEqual(result['summary']['pdf_files_present'], 0)
        self.assertEqual(result['items'][0]['pdf_file_status'], 'unverified')

    def test_registry_only_qdrant_only_and_unavailable_are_explicit(self):
        self.assertEqual(self.status(counts={})['items'][0]['index_status'], 'index_missing')
        qdrant_only = self.status(counts={"iam": 2, "tir": 42})
        self.assertEqual(next(x for x in qdrant_only['items'] if x['entity_id'] == 'iam')['index_status'], 'registry_missing')
        unavailable = self.status(counts=None)
        self.assertFalse(unavailable['summary']['qdrant_available'])
        self.assertIsNone(unavailable['summary']['indexed_current'])
        self.assertEqual(unavailable['items'][0]['index_status'], 'qdrant_unavailable')

    def test_agent_tool_returns_same_authoritative_count(self):
        tool = local_library_tools()[0]
        result = tool.handler({'catalog_conn': self.catalog, 'downloads_conn': self.downloads,
                               'parsed_conn': self.parsed, 'document_chunk_counts': {'tir': 42},
                               'pipeline_fingerprint': 'fingerprint'})
        self.assertTrue(result.ok)
        self.assertEqual(result.data, self.status())
        self.assertIn('当前有效索引 1 篇', result.summary)
