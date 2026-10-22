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


class StatisticsContractTest(unittest.TestCase):
    setUpClass = classmethod(CapabilityTest.setUpClass.__func__)
    tearDownClass = classmethod(CapabilityTest.tearDownClass.__func__)
    def test_live_summary_uses_research_denominator(self):
        from backend.catalog.metrics import corpus_summary
        data = corpus_summary(self.con)
        self.assertEqual(data['research_paper_count'], 3)
        self.assertEqual(data['entity_count'], data['research_paper_count'] + data['non_paper_count'])
        self.assertEqual(data['abstract_count'] + data['missing_abstract_count'], 3)
        self.assertEqual(data['classified_count'] + data['pending_count'], 3)

    def test_partial_year_does_not_produce_growth(self):
        from unittest.mock import patch
        from backend.capabilities.stats import trend
        with patch('backend.capabilities.stats._group_counts', return_value=[{'key':2023,'count':2},{'key':2025,'count':4},{'key':2026,'count':9}]), patch('backend.capabilities.stats._denominator_by', return_value={2023:10,2025:10,2026:20}):
            result = trend(self.con, {})
            self.assertEqual(result['growth']['last_year'], 2025)
            self.assertEqual(result['growth']['change_rate'], 1)
            result = trend(self.con, {'filters':{'years':[2023,2026]}})
            self.assertIsNone(result['growth']['change_rate'])
            self.assertFalse(result['growth']['comparable'])

    def test_zero_endpoint_is_not_omitted(self):
        from unittest.mock import patch
        from backend.capabilities.stats import trend
        with patch('backend.capabilities.stats._group_counts', return_value=[{'key':2024,'count':2}]), patch('backend.capabilities.stats._denominator_by', return_value={2023:10,2024:10,2025:20}):
            result = trend(self.con, {})
            self.assertEqual([r['count'] for r in result['items']], [0,2,0])
            self.assertIsNone(result['growth']['change_rate'])
            self.assertEqual(result['growth']['reason'], 'zero_baseline')

    def test_search_contract_identifies_relaxation_and_unsupported_input(self):
        from backend.catalog.search import match_metadata, build_fts_query_or
        from backend.catalog import queries
        result = match_metadata(self.con, 'absent agent', build_fts_query_or('absent agent'))
        self.assertTrue(result['relaxed'])
        self.assertEqual(result['count_kind'], 'candidates')
        self.assertEqual(match_metadata(self.con, '图像超分', '')['mode'], 'unsupported_tokens')
        self.assertEqual(queries.papers(self.con)['count_unit'], 'source_records')

    def test_real_fts_response_distinguishes_candidates(self):
        from backend.catalog import search, queries
        search.rebuild_index(self.con)
        result = queries.papers(self.con, search='zzzzunfindable paper')
        self.assertGreater(result['total'], 0)
        self.assertEqual(result['match']['mode'], 'relaxed_any')
        result = queries.papers(self.con, search='图像超分')
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['match']['mode'], 'unsupported_tokens')
        result = queries.papers(self.con, search='中文 paper')
        self.assertEqual(result['match']['ignored_fragments'], ['中文'])

    def test_pending_entities_are_not_classified(self):
        from backend.catalog.metrics import corpus_summary
        self.con.execute('SAVEPOINT coverage_test')
        try:
            columns = [r[1] for r in self.con.execute('PRAGMA table_info(area_definitions)')]
            selected = ["'pending_classification'" if c == 'area_id' else c for c in columns]
            self.con.execute(f"INSERT OR IGNORE INTO area_definitions ({','.join(columns)}) SELECT {','.join(selected)} FROM area_definitions WHERE area_id='llm'")
            self.con.execute("UPDATE entity_area_assignments SET area_id='pending_classification' WHERE area_id='llm'")
            data = corpus_summary(self.con)
            self.assertEqual(data['pending_count'], 1)
            self.assertEqual(data['classified_count'], 2)
            self.assertEqual(data['classification_coverage_rate'], 66.67)
        finally:
            self.con.execute('ROLLBACK TO coverage_test')
            self.con.execute('RELEASE coverage_test')

    def test_taxonomy_does_not_count_catchall_buckets_as_areas(self):
        from unittest.mock import patch
        from backend.capabilities.taxonomy import list_taxonomy
        topics = [{'topic_id': key, 'name': key, 'name_en': key, 'entity_count': 0, 'subtopics': []}
                  for key in ['llm', 'other', 'pending_classification']]
        with patch('backend.capabilities.taxonomy.area_queries.areas_overview', return_value={'label': {'id':'test'}, 'topics': topics}):
            data = list_taxonomy(self.con, {})
        self.assertEqual(data['area_count'], 1)
        self.assertEqual(len(data['areas']), 3)

    def test_publication_review_is_independent_of_source_list(self):
        from backend.catalog.publication import review
        self.assertEqual(review('ICML', 2026, 'PTTa3U29NR')['status'], 'verified_journal_presentation')
        self.assertEqual(review('ICML', 2026, 'virtual-71804')['status'], 'pending_publication_verification')
        self.assertEqual(review('ICML', 2025, 'virtual-71800')['status'], 'not_individually_reviewed')
        journal = review('The Annals of Statistics', 2026, '10.1214/26-aos2643')
        self.assertEqual(journal['status'], 'verified_journal_presentation')
        self.assertEqual(journal['publication_year'], 2026)
        self.assertEqual(journal['source_record_id'], review('ICML', 2026, 'virtual-71805')['source_record_id'])

    def test_catalog_returns_publication_review(self):
        from backend.catalog import queries
        from backend.capabilities.catalog import get_paper
        rows = queries.papers(self.con)['items']
        self.assertEqual(rows[0]['publication_review']['status'], 'not_individually_reviewed')
        paper = get_paper(self.con, {'entity_id': rows[0]['entity_id']})
        self.assertTrue(all('publication_review' in r for r in paper['appearances']))
