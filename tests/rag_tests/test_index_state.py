from __future__ import annotations

import shutil
import sqlite3
import tempfile
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from backend.rag import parse_store, service
from raglib import InMemoryStore, MarkdownSplitter, Pipeline


class Embedder:
    model = "test-embed"
    dim = 2
    def __init__(self): self.calls = 0
    def embed_texts(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]
    def embed_query(self, text): return [1.0, 0.0]


class IndexStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "parsed"
        self.conn = parse_store.connect(self.root / "parsed.sqlite")
        parse_store.initialize(self.conn)
        self.patch = mock.patch.object(parse_store, "PARSED_ROOT", self.root)
        self.patch.start()
        path = parse_store.parsed_path("e1")
        path.parent.mkdir(parents=True)
        path.write_text("# T\n\nbody", encoding="utf-8")
        parse_store.mark(self.conn, "e1", "success", markdown_path=str(path), char_count=9)
        self.catalog = sqlite3.connect(":memory:")
        self.catalog.row_factory = sqlite3.Row
        self.catalog.execute("CREATE TABLE paper_entities (entity_id TEXT, canonical_title TEXT)")
        self.catalog.execute("INSERT INTO paper_entities VALUES ('e1', 'Title')")
        self.embedder = Embedder()
        self.pipeline = Pipeline(MarkdownSplitter(), self.embedder, InMemoryStore())

    def tearDown(self):
        self.patch.stop(); self.conn.close(); self.catalog.close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_content_and_fingerprint_skips_second_embedding(self):
        first = service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        second = service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        self.assertEqual(first["documents"], 1)
        self.assertEqual(second["documents"], 0)
        self.assertEqual(second["already_indexed"], 1)
        self.assertEqual(self.embedder.calls, 1)

    def test_fingerprint_change_marks_stale_and_reindexes(self):
        service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        changed = Pipeline(MarkdownSplitter(max_chars=99), self.embedder, self.pipeline.store)
        result = service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=changed)
        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["stale_ids"], ["e1"])

    def test_canonical_id_is_the_vector_document_id(self):
        service.ingest_parsed(self.catalog, self.conn, ["e1"], canonical_ids={"e1": "canon"}, pipeline=self.pipeline)
        self.assertEqual(self.pipeline.store.document_ids(), {"canon"})

    def test_reparse_without_reindex_is_never_current(self):
        service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        path = parse_store.parsed_path("e1")
        path.write_text("# T\n\nnew body", encoding="utf-8")
        parse_store.mark(self.conn, "e1", "success", markdown_path=str(path),
                         parsed_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        state = parse_store.index_state(self.conn, "e1")
        self.assertNotEqual(state["parsed_sha256"], state["indexed_parsed_sha256"])
        result = service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["stale_ids"], ["e1"])

    def test_missing_chunk_cannot_skip_repair(self):
        service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        self.pipeline.store.delete("e1")
        result = service.ingest_parsed(self.catalog, self.conn, ["e1"], pipeline=self.pipeline)
        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["already_indexed"], 0)
