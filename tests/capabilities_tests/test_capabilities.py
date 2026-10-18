import tempfile
import unittest
from pathlib import Path

from backend.capabilities import REGISTRY
from backend.catalog import labels as label_import
from tests.labels_tests.test_import_labels import CREATED, make_db


class CapabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        con, a, d, n, g, m = make_db(Path(cls._tmp.name))
        label_import.import_labels(con, "rel", CREATED, areas_labels=a, directions_labels=d,
                                   non_paper_labels=n, golden=g, metrics=m)
        cls.con = con

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    def call(self, name, args=None):
        return REGISTRY.call(self.con, name, args or {})

    def test_counts_exclude_non_paper(self):
        result = self.call("count_papers")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 3)
        self.assertEqual(result.meta["label_release_id"], "labels-20261018-areas-v1.5-directions-v1.1")
        grouped = self.call("count_papers", {"filters": {"area_ids": ["ml_foundations"]}})
        self.assertEqual(grouped.data["total"], 1)

    def test_share_and_trend(self):
        share = self.call("share", {"filters": {"area_ids": ["llm"]}, "by": "area"})
        self.assertEqual(share.data["items"][0]["count"], 1)
        self.assertEqual(share.data["denominator_total"], 3)
        trend = self.call("trend", {"filters": {}})
        self.assertEqual([(x["key"], x["count"]) for x in trend.data["items"]], [(2023, 1), (2024, 2)])
        self.assertEqual(trend.data["items"][1]["total"], 2)

    def test_search_and_get_paper(self):
        found = self.call("search_papers", {"filters": {"query": "Paper One"}})
        self.assertEqual(found.data["total"], 1)
        eid = found.data["items"][0]["entity_id"]
        paper = self.call("get_paper", {"entity_id": eid})
        self.assertTrue(paper.ok)
        self.assertEqual(paper.data["labels"]["area_id"], "ml_foundations")
        self.assertEqual(paper.data["labels"]["direction_id"], "ml_foundations-d01")

    def test_name_filters(self):
        by_name = self.call("count_papers", {"filters": {"area_names": ["机器学习"]}})
        self.assertEqual(by_name.data["total"], 1)
        by_id = self.call("count_papers", {"filters": {"area_ids": ["ml_foundations"]}})
        self.assertEqual(by_name.data["total"], by_id.data["total"])

    def test_taxonomy_and_errors(self):
        taxonomy = self.call("list_taxonomy", {"area_id": "llm"})
        self.assertEqual(taxonomy.data["area_count"], 1)
        self.assertEqual(taxonomy.data["areas"][0]["area_id"], "llm")
        unknown = self.call("does_not_exist")
        self.assertFalse(unknown.ok)
        self.assertIn("unknown capability", unknown.error)
        bad = self.call("count_papers", {"filters": {"nope": 1}})
        self.assertFalse(bad.ok)
        self.assertIn("unknown filter", bad.error)

    def test_catalog_papers_area_filter(self):
        from backend.catalog import queries
        page = queries.papers(self.con, area="ml_foundations")
        self.assertEqual(page["total"], 1)
        self.assertEqual(queries.papers_entity_ids(self.con, area="ml_foundations")["total"], 1)
        self.assertEqual(queries.papers(self.con, area="no_such_area")["total"], 0)

    def test_web_agent_exposes_same_capabilities(self):
        from backend.agent.tools import build_tools
        tools = {t.name: t for t in build_tools()}
        for name in ("count_papers", "share_papers", "trend_papers", "top_values"):
            self.assertIn(name, tools)
        result = tools["count_papers"].handler({"catalog_conn": self.con}, filters={"area_ids": ["ml_foundations"]})
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 1)
        self.assertIn("label_release_id", result.data["meta"])

    def test_topic_recall_falls_back_to_like(self):
        result = self.call("recall_topic", {"query": "Paper One"})
        self.assertTrue(result.ok)
        self.assertEqual(result.data["candidate_count"], 1)
        self.assertEqual(result.data["method"], "like-fallback")
        self.assertEqual(result.data["verification"]["status"], "not_verified")

    def test_compare_and_cooccurrence(self):
        compared = self.call("compare", {"groups": [{"label": "ml", "filters": {"area_ids": ["ml_foundations"]}},
                                                   {"label": "llm", "filters": {"area_ids": ["llm"]}}]})
        self.assertEqual(len(compared.data["series"]), 2)
        co = self.call("cooccurrence", {"field_a": "area", "field_b": "direction"})
        self.assertEqual(sum(x["count"] for x in co.data["items"]), 3)


if __name__ == "__main__":
    unittest.main()
