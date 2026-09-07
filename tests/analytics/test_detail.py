"""#58 detail pages: invariants and real-database tests for the topic_detail / venue_detail / venue_papers endpoints."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from backend.analytics import queries as analytics_queries


DATABASE = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


class TopicDetailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DATABASE.exists():
            raise unittest.SkipTest("catalog.sqlite 尚未构建")
        cls.connection = sqlite3.connect(DATABASE)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "connection"):
            cls.connection.close()

    def test_family_basic_shape(self) -> None:
        d = analytics_queries.topic_detail(self.connection, "f1_ml_basis")
        self.assertEqual(d["topic"]["topic_id"], "f1_ml_basis")
        self.assertEqual(d["topic"]["level"], "family")
        self.assertGreater(d["metrics"]["entity_count"], 0)
        self.assertEqual(len(d["subtopics"]), 4)  # 4 leaves under the family root
        self.assertEqual(len(d["top_venues"]), 10)
        self.assertGreaterEqual(len(d["tech_tags"]), 1)
        self.assertEqual(len(d["trend"]["items"]), 4)  # 4 years
        self.assertGreater(d["papers_total"], 0)
        # papers_total should reconcile with metrics.entity_count (primary entities of the same node)
        self.assertEqual(d["papers_total"], d["metrics"]["entity_count"])

    def test_leaf_basic_shape(self) -> None:
        d = analytics_queries.topic_detail(self.connection, "ml_paradigm")
        self.assertEqual(d["topic"]["level"], "leaf")
        self.assertEqual(len(d["subtopics"]), 1)  # the leaf returns itself
        self.assertEqual(d["subtopics"][0]["topic_id"], "ml_paradigm")

    def test_subtopics_share_rate_invariants(self) -> None:
        d = analytics_queries.topic_detail(self.connection, "f1_ml_basis")
        total = d["metrics"]["entity_count"]
        for item in d["subtopics"]:
            self.assertGreaterEqual(item["entity_count"], 0)
            if total:
                self.assertAlmostEqual(item["share_rate"], round(100 * item["entity_count"] / total, 1), places=1)
        # the sum of entity_count over the 4-year trend should equal metrics.entity_count
        trend_total = sum(item["entity_count"] for item in d["trend"]["items"])
        self.assertEqual(trend_total, total)

    def test_unknown_topic_raises(self) -> None:
        with self.assertRaises(ValueError):
            analytics_queries.topic_detail(self.connection, "no_such_topic")

    def test_empty_topic_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            analytics_queries.topic_detail(self.connection, "")

    def test_topic_papers_total_pages_math(self) -> None:
        # regression: total_pages was computed from `page` instead of `total`,
        # so multi-page topic lists reported "第 1 / 1 页" and blocked paging.
        page_size = 20
        for topic_id in ("f1_ml_basis", "f2_llm_models"):
            p = analytics_queries.topic_papers(self.connection, topic_id=topic_id, page=1, page_size=page_size)
            self.assertEqual(p["total_pages"], (p["total"] + page_size - 1) // page_size)
            self.assertEqual(len(p["items"]), min(page_size, p["total"]))

    def test_topic_papers_second_page_is_distinct_and_reachable(self) -> None:
        page_size = 20
        p1 = analytics_queries.topic_papers(self.connection, topic_id="f2_llm_models", page=1, page_size=page_size)
        if p1["total_pages"] < 2:
            self.skipTest("样本主题不足两页")
        p2 = analytics_queries.topic_papers(self.connection, topic_id="f2_llm_models", page=2, page_size=page_size)
        self.assertEqual(p2["page"], 2)
        ids1 = {i["entity_id"] for i in p1["items"]}
        ids2 = {i["entity_id"] for i in p2["items"]}
        self.assertFalse(ids1 & ids2)


class VenueDetailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DATABASE.exists():
            raise unittest.SkipTest("catalog.sqlite 尚未构建")
        cls.connection = sqlite3.connect(DATABASE)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "connection"):
            cls.connection.close()

    def test_venue_basic_shape(self) -> None:
        d = analytics_queries.venue_detail(self.connection, "ICML")
        self.assertEqual(d["venue"]["venue"], "ICML")
        self.assertEqual(d["venue"]["venue_type"], "conference")
        self.assertEqual(d["metrics"]["entity_count"], d["metrics"]["record_count"])  # ICML 1 record / entity
        self.assertEqual(d["metrics"]["final_count"] + d["metrics"]["rolling_count"], d["metrics"]["record_count"])
        self.assertEqual(len(d["topic_mix"]), 11)  # 10 families + unclassified
        self.assertEqual(len(d["top_topics"]), 10)
        self.assertEqual(len(d["trend"]["years"]), 4)
        self.assertGreater(d["papers_total"], 0)

    def test_venue_topic_mix_invariant(self) -> None:
        d = analytics_queries.venue_detail(self.connection, "ICML")
        total = d["metrics"]["entity_count"]
        mix_sum = sum(item["entity_count"] for item in d["topic_mix"])
        self.assertEqual(mix_sum, total, f"mix_sum={mix_sum} vs entity_count={total}")
        # the sum of the bucket share_rates should be close to 100
        share_sum = sum(item["share_rate"] for item in d["topic_mix"])
        self.assertAlmostEqual(share_sum, 100.0, delta=0.5)

    def test_unknown_venue_returns_empty(self) -> None:
        d = analytics_queries.venue_detail(self.connection, "NOPE_FAKE_VENUE")
        self.assertEqual(d["metrics"]["entity_count"], 0)
        self.assertEqual(d["topic_mix"], [])
        self.assertEqual(d["papers_total"], 0)

    def test_venue_papers_basic(self) -> None:
        vp = analytics_queries.venue_papers(self.connection, venue="ICML", page=1, page_size=5)
        self.assertEqual(vp["total"], 14109)
        self.assertEqual(len(vp["items"]), 5)
        self.assertEqual(vp["page"], 1)
        # each has an entity_id (dedup logic)
        for item in vp["items"]:
            self.assertIn("entity_id", item)
            self.assertIn("title", item)
            self.assertEqual(item["venue"], "ICML")

    def test_venue_papers_pagination(self) -> None:
        vp1 = analytics_queries.venue_papers(self.connection, venue="ICML", page=1, page_size=10)
        vp2 = analytics_queries.venue_papers(self.connection, venue="ICML", page=2, page_size=10)
        self.assertNotEqual(vp1["items"][0]["record_id"], vp2["items"][0]["record_id"])
        self.assertEqual(vp1["total"], vp2["total"])


if __name__ == "__main__":
    unittest.main()
