from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from backend.catalog import queries, search

DATABASE = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


class NormalizeTest(unittest.TestCase):
    def test_camel_case_split(self) -> None:
        self.assertEqual(search.normalize_text("4KAgent"), "4k agent")
        self.assertEqual(search.normalize_text("SuperResolution"), "super resolution")
        self.assertEqual(search.normalize_text("agenticAny"), "agentic any")

    def test_letter_digit_boundary_split(self) -> None:
        self.assertEqual(search.normalize_text("Super4K"), "super 4k")
        # no split from digit to letter: 4K stays a single token
        self.assertEqual(search.normalize_text("4K"), "4k")

    def test_nfkc_casefold(self) -> None:
        self.assertEqual(search.normalize_text("ＳｕｐｅｒＡＩ"), "super ai")

    def test_tokenize(self) -> None:
        self.assertEqual(search.tokenize("4k agent: agentic any image"), ["4k", "agent", "agentic", "any", "image"])


class FtsQueryTest(unittest.TestCase):
    def test_basic_tokens(self) -> None:
        fts, tokens = search.build_fts_query("4K agent")
        self.assertEqual(tokens, ["4k", "agent"])
        self.assertIn('"4k"*', fts)
        self.assertIn(" AND ", fts)

    def test_synonym_expansion(self) -> None:
        fts, tokens = search.build_fts_query("SR image")
        self.assertIn('"sr"*', fts)
        self.assertIn('"super resolution"', fts)  # synonym phrase expansion

    def test_stopwords_filtered(self) -> None:
        fts, tokens = search.build_fts_query("a study of the agent")
        self.assertEqual(tokens, ["study", "agent"])
        self.assertNotIn("the", fts)

    def test_all_stopwords_empty(self) -> None:
        fts, tokens = search.build_fts_query("the of and")
        self.assertEqual(fts, "")
        self.assertEqual(tokens, [])

    def test_prefix_or_degradation(self) -> None:
        fts = search.build_fts_query_or("quantum 4k")
        self.assertIn(" OR ", fts)
        self.assertIn('("quantum"*)', fts)


class FtsIndexTest(unittest.TestCase):
    """Verify index build / increment / intent-guessing retrieval on an in-memory DB."""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE paper_records (record_id TEXT PRIMARY KEY, title TEXT,"
            " abstract TEXT, authors TEXT, paper_id TEXT, doi TEXT, arxiv_id TEXT)"
        )
        search._exec_ddl(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def _insert(self, record_id, title, abstract="", authors="", paper_id="", doi="", arxiv_id="") -> None:
        self.conn.execute(
            "INSERT INTO paper_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_id, title, abstract, authors, paper_id, doi, arxiv_id),
        )
        self.conn.commit()
        search.index_record(self.conn, record_id)

    def test_rebuild_and_match(self) -> None:
        self._insert("r1", "4KAgent: Agentic Any Image to 4K Super-Resolution", abstract="SR upscaling")
        self._insert("r2", "Transformer for Object Detection", abstract="DETR")
        ids = search.resolve(self.conn, "4K agent")
        self.assertIn("r1", ids)
        self.assertNotIn("r2", ids)

    def test_synonym_intent(self) -> None:
        self._insert("r1", "Diffusion Models for Image Generation")
        ids = search.resolve(self.conn, "diffusion model")
        self.assertIn("r1", ids)

    def test_or_degradation(self) -> None:
        self._insert("r1", "Quantum Entanglement Networks")
        self._insert("r2", "4K Video Upscaling")
        # AND (quantum AND 4k) has no intersection -> after OR degradation, both match
        ids = search.resolve(self.conn, "quantum 4k")
        self.assertIn("r1", ids)
        self.assertIn("r2", ids)

    def test_prefix_match(self) -> None:
        self._insert("r1", "DiffusionDet: Diffusion Model for Object Detection")
        ids = search.resolve(self.conn, "diffus")
        self.assertIn("r1", ids)

    def test_remove_record(self) -> None:
        self._insert("r1", "Temporary Paper")
        self.assertIn("r1", search.resolve(self.conn, "temporary"))
        search.remove_record(self.conn, "r1")
        self.assertNotIn("r1", search.resolve(self.conn, "temporary"))

    def test_index_record_idempotent(self) -> None:
        self._insert("r1", "Idempotent Paper")
        search.index_record(self.conn, "r1")  # re-indexing does not error and does not duplicate
        ids = search.resolve(self.conn, "idempotent")
        self.assertEqual(ids.count("r1"), 1)


@unittest.skipUnless(DATABASE.exists(), "catalog.sqlite 尚未构建")
class CatalogSearchIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = sqlite3.connect(DATABASE)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_fts_index_exists(self) -> None:
        has = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fts_papers'"
        ).fetchone()
        self.assertIsNotNone(has)

    def test_4k_agent_hits_4kagent(self) -> None:
        """The user's original requirement: '4K agent' must be able to retrieve '4KAgent: Agentic...'"""
        result = queries.search_papers(self.connection, query="4K agent", page_size=5)
        self.assertGreater(result["total"], 0)
        titles = [item["title"].lower() for item in result["items"]]
        self.assertTrue(any("4kagent" in t or "4k agent" in t for t in titles))

    def test_synonym_sr(self) -> None:
        result = queries.search_papers(self.connection, query="SR image", page_size=3)
        self.assertGreater(result["total"], 0)

    def test_or_degradation_in_search_papers(self) -> None:
        result = queries.search_papers(self.connection, query="quantum entanglement 4k", page_size=3)
        self.assertGreater(result["total"], 0)

    def test_search_with_filters_and_facets(self) -> None:
        result = queries.search_papers(
            self.connection, query="super resolution", venues=["CVPR"], page_size=3, aggregate=["venue"]
        )
        self.assertGreater(result["total"], 0)
        self.assertIn("venue", result["facets"])
        self.assertEqual(result["facets"]["venue"]["CVPR"], result["total"])

    def test_papers_search_fts_ordered(self) -> None:
        result = queries.papers(self.connection, search="4K agent", page_size=3)
        self.assertGreater(result["total"], 0)
        self.assertEqual(result["items"][0]["title"].lower(), "4kagent: agentic any image to 4k super-resolution")

    def test_no_query_returns_all(self) -> None:
        result = queries.search_papers(self.connection, query="", page_size=3)
        self.assertGreater(result["total"], 0)


if __name__ == "__main__":
    unittest.main()
