from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from backend.catalog import paper_search

DATABASE = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


@unittest.skipUnless(DATABASE.exists(), "catalog.sqlite 尚未构建")
class PaperSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_all_terms_match_keeps_every_keyword(self) -> None:
        result = paper_search.search(self.connection, query="4K agent", page_size=3)
        self.assertEqual(result["match"]["dropped"], [])
        self.assertIn("4kagent", result["items"][0]["title"].lower())

    def test_relaxation_drops_one_term_instead_of_or(self) -> None:
        # No paper contains all five words; the old OR fallback matched ~35k papers.
        result = paper_search.search(self.connection, query="agentic image restoration tool orchestration", page_size=5)
        self.assertLess(result["total"], 100)
        self.assertEqual(len(result["match"]["dropped"]), 1)
        self.assertIn("restoration", result["match"]["terms"])
        self.assertIn("note", result["match"])

    def test_items_carry_labels_and_snippet(self) -> None:
        result = paper_search.search(self.connection, query="video super resolution",
                                     filters={"area_ids": ["cv_lowlevel"]}, page_size=2, aggregate=["direction"])
        item = result["items"][0]
        self.assertTrue(item["area"] and item["direction"] and item["snippet"])
        self.assertTrue(result["facets"]["direction"])

    def test_chinese_query_asks_for_english(self) -> None:
        result = paper_search.search(self.connection, query="图像超分", page_size=3)
        self.assertEqual(result["total"], 0)
        self.assertIn("英文", result["match"]["note"])

    def test_filters_only_counts_entities_once(self) -> None:
        result = paper_search.search(self.connection, filters={"direction_ids": ["cv_lowlevel-d15-video"]}, page_size=0)
        expected = self.connection.execute(
            """SELECT COUNT(*) FROM entity_direction_assignments d JOIN entity_area_assignments a USING(entity_id)
               WHERE d.direction_id = 'cv_lowlevel-d15-video' AND a.is_non_paper = 0""").fetchone()[0]
        self.assertEqual(result["total"], expected)


if __name__ == "__main__":
    unittest.main()
