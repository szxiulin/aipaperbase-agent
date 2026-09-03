from __future__ import annotations

import sqlite3
import json
import unittest
from pathlib import Path

from backend.catalog import queries
from backend.analytics import queries as analytics_queries
from backend.analytics.topics import load_topics


DATABASE = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


class CatalogDatabaseTest(unittest.TestCase):
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

    def test_database_integrity(self) -> None:
        self.assertEqual(self.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_current_catalog_baseline(self) -> None:
        result = queries.summary(self.connection)
        self.assertEqual(result["record_count"], 122_571)
        self.assertEqual(result["edition_count"], 107)
        self.assertEqual(result["conference_count"], 20)
        self.assertEqual(result["journal_count"], 10)
        self.assertEqual(result["final_count"], 112_006)
        self.assertEqual(result["rolling_count"], 10_565)
        self.assertEqual(result["abstract_count"], 114_255)
        self.assertEqual(result["entity_count"], 121_742)
        self.assertEqual(result["merged_entity_count"], 829)
        self.assertEqual(result["linked_record_count"], 1_658)

    def test_no_blocking_quality_issues(self) -> None:
        count = self.connection.execute(
            "SELECT COUNT(*) FROM quality_issues WHERE severity = 'error'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_paper_filter_is_paginated(self) -> None:
        result = queries.papers(self.connection, venue="CVPR", year=2025, page_size=10)
        self.assertGreater(result["total"], 0)
        self.assertEqual(len(result["items"]), 10)
        self.assertTrue(all(item["venue"] == "CVPR" and item["year"] == 2025 for item in result["items"]))

    def test_abstract_provenance_is_complete(self) -> None:
        invalid = self.connection.execute(
            """SELECT COUNT(*) FROM paper_records
               WHERE (abstract <> '' AND (
                         abstract_source_name = '' OR abstract_source_url = '' OR
                         abstract_source_tier = '' OR abstract_fetched_at = ''
                     ))
                  OR (abstract = '' AND (
                         abstract_source_name <> '' OR abstract_source_url <> '' OR
                         abstract_source_tier <> '' OR abstract_fetched_at <> ''
                     ))"""
        ).fetchone()[0]
        self.assertEqual(invalid, 0)

    def test_abstract_search_and_filter(self) -> None:
        result = queries.papers(
            self.connection,
            venue="CVPR",
            year=2024,
            has_abstract="yes",
            search="chrome ball",
            page_size=10,
        )
        self.assertGreater(result["total"], 0)
        self.assertTrue(all(item["abstract"] for item in result["items"]))

    def test_every_record_maps_to_exactly_one_entity(self) -> None:
        record_count = self.connection.execute("SELECT COUNT(*) FROM paper_records").fetchone()[0]
        membership_count = self.connection.execute("SELECT COUNT(*) FROM entity_memberships").fetchone()[0]
        distinct_records = self.connection.execute(
            "SELECT COUNT(DISTINCT record_id) FROM entity_memberships"
        ).fetchone()[0]
        self.assertEqual(membership_count, record_count)
        self.assertEqual(distinct_records, record_count)

    def test_doi_groups_are_linked_without_deleting_records(self) -> None:
        invalid = self.connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT p.doi
                   FROM paper_records p
                   JOIN entity_memberships m ON m.record_id = p.record_id
                   WHERE p.doi <> ''
                   GROUP BY p.doi
                   HAVING COUNT(*) > 1 AND COUNT(DISTINCT m.entity_id) <> 1
               )"""
        ).fetchone()[0]
        self.assertEqual(invalid, 0)
        linked = self.connection.execute(
            "SELECT COUNT(*) FROM entity_memberships WHERE match_method = 'doi'"
        ).fetchone()[0]
        self.assertEqual(linked, 1_658)

    def test_uncertain_relations_remain_pending(self) -> None:
        quality = queries.entity_quality(self.connection)
        self.assertEqual(quality["pending_review_count"], 32)
        conflicts = self.connection.execute(
            """SELECT COUNT(*) FROM entity_review_candidates
               WHERE candidate_method = 'arxiv_conflict' AND confidence = 'conflict'
                 AND review_status = 'pending'"""
        ).fetchone()[0]
        self.assertEqual(conflicts, 2)
        merged_candidates = self.connection.execute(
            """SELECT COUNT(*)
               FROM entity_review_candidates c
               JOIN entity_memberships l ON l.record_id = c.left_record_id
               JOIN entity_memberships r ON r.record_id = c.right_record_id
               WHERE l.entity_id = r.entity_id"""
        ).fetchone()[0]
        self.assertEqual(merged_candidates, 0)

    def test_entity_browser_returns_members_and_evidence(self) -> None:
        result = queries.entities(self.connection, entity_status="merged", page_size=10)
        self.assertEqual(result["total"], 829)
        self.assertEqual(len(result["items"]), 10)
        self.assertTrue(all(item["record_count"] > 1 for item in result["items"]))
        self.assertTrue(all(item["members"] for item in result["items"]))
        self.assertTrue(all(item["members"][0]["evidence_value"] for item in result["items"]))

    def test_topic_configuration_and_run_are_versioned(self) -> None:
        config, topics = load_topics()
        self.assertEqual(config["version"], "topic-tree-v2-20260902")
        self.assertEqual(len([topic for topic in topics if not topic.parent_topic_id]), 10)
        run = self.connection.execute(
            "SELECT * FROM analysis_runs WHERE analysis_type = 'topic_classification'"
        ).fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "ready")
        self.assertEqual(run["config_version"], config["version"])
        self.assertEqual(run["assignment_count"], 93_400)

    def test_topic_baseline_counts_and_hierarchy(self) -> None:
        overview = analytics_queries.topic_overview(self.connection)
        self.assertEqual(overview["classified_entity_count"], 72_999)
        self.assertEqual(overview["multi_label_rate"], 22.9)
        counts = {item["topic_id"]: item["entity_count"] for item in overview["topics"]}
        self.assertEqual(counts, {
            "f1_ml_basis": 16_132,
            "f2_llm_models": 6_442,
            "f3_agent_systems": 2_043,
            "f4_nlp_tasks": 6_316,
            "f5_lowlevel_vision": 4_366,
            "f6_generation": 7_387,
            "f7_highlevel_vision": 14_701,
            "f8_3d": 6_103,
            "f9_multimodal": 8_542,
            "f10_speech": 967,
        })
        # v2 hierarchy invariant: assignments only land on leaves; every assigned topic has a full ancestor path
        dangling_ancestors = self.connection.execute(
            """SELECT COUNT(*) FROM entity_topic_assignments a
               LEFT JOIN topic_ancestors x ON x.topic_id = a.topic_id
               WHERE x.topic_id IS NULL"""
        ).fetchone()[0]
        self.assertEqual(dangling_ancestors, 0)
        non_leaf_assignments = self.connection.execute(
            """SELECT COUNT(*) FROM entity_topic_assignments a
               WHERE a.topic_id IN (
                   SELECT parent_topic_id FROM topic_definitions WHERE parent_topic_id <> ''
               )"""
        ).fetchone()[0]
        self.assertEqual(non_leaf_assignments, 0)  # family/direction/container are pure groupings; no direct assignment allowed

    def test_topic_assignments_have_explainable_evidence(self) -> None:
        rows = self.connection.execute(
            "SELECT evidence_json, score FROM entity_topic_assignments"
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            evidence = json.loads(row["evidence_json"])
            self.assertTrue(evidence)
            self.assertGreater(row["score"], 0)
            self.assertTrue(all("signal" in item and "field" in item and "points" in item for item in evidence))

    def test_topic_trends_and_drilldown_use_entities(self) -> None:
        trends = analytics_queries.topic_trends(self.connection)
        self.assertEqual([item["year"] for item in trends["years"]], [2023, 2024, 2025, 2026])
        result = analytics_queries.topic_papers(
            self.connection,
            topic_id="f3_agent_systems",
            year=2026,
            page_size=10,
        )
        self.assertEqual(result["total"], 1_007)
        self.assertEqual(len(result["items"]), 10)
        self.assertTrue(all(item["evidence"] and item["appearances"] for item in result["items"]))

    def test_fixc_agent_overinflation_is_gone(self) -> None:
        """fix C acceptance: Agent over-inflation (historically at a 1,788/1,390 level) recedes; the multi-agent leaf no longer swallows MARL."""
        by_leaf = {}
        for item in self.connection.execute(
            """SELECT x.topic_id, COUNT(DISTINCT a.entity_id) AS n
               FROM entity_topic_assignments a
               JOIN topic_ancestors x ON x.topic_id = a.topic_id
               JOIN entity_memberships m ON m.entity_id = a.entity_id
               JOIN paper_records p ON p.record_id = m.record_id
               WHERE a.role = 'primary' AND x.ancestor_topic_id = 'f3_agent_systems' AND p.year = 2026
               GROUP BY x.topic_id"""
        ):
            by_leaf[item["topic_id"]] = item["n"]
        # f3 family 2026 already fell from 1,390 pre-fix C to 1,007 (see test_topic_trends...),
        # multi_agent leaf drops from 542-level MARL inflation to <300; agent_mechanism stays at a healthy level
        self.assertLess(by_leaf.get("multi_agent", 0), 300)
        self.assertLess(by_leaf.get("agent_mechanism", 0), 350)

    def test_topic_evaluation_is_fixed_stratified_and_traceable(self) -> None:
        result = analytics_queries.topic_evaluation(self.connection)
        self.assertEqual(result["evaluation"]["evaluation_version"], "topic-audit-v2")
        self.assertEqual(result["overall"]["sampled_count"], 300)
        self.assertEqual(result["overall"]["reviewed_count"], 0)
        self.assertEqual(result["overall"]["pending_count"], 300)
        self.assertEqual(result["evaluation"]["reviewer_type"], "model_assisted")
        for topic in result["topics"]:
            self.assertEqual(topic["sampled_count"], 100)
            self.assertEqual(topic["reviewed_count"], 0)
            self.assertEqual(
                {band["score_band"]: band["sampled_count"] for band in topic["bands"]},
                {"high": 30, "middle": 30, "boundary": 40},
            )

    def test_topic_evaluation_precision_is_reported_only_after_review(self) -> None:
        result = analytics_queries.topic_evaluation(self.connection)
        self.assertEqual(result["overall"]["reviewed_count"], 0)
        self.assertIsNone(result["overall"]["preliminary_precision"])
        for topic in result["topics"]:
            self.assertIsNone(topic["preliminary_precision"])

    def test_topic_evaluation_review_provenance_is_complete(self) -> None:
        invalid = self.connection.execute(
            """SELECT COUNT(*) FROM topic_evaluation_samples
               WHERE verdict <> 'pending' AND (
                 rationale = '' OR reviewer_type = '' OR reviewer_name = '' OR
                 evidence_level = '' OR reviewed_at = '' OR evidence_json = ''
               )"""
        ).fetchone()[0]
        self.assertEqual(invalid, 0)
        invalid_entities = self.connection.execute(
            """SELECT COUNT(*) FROM topic_evaluation_samples s
               LEFT JOIN paper_entities e ON e.entity_id = s.entity_id
               WHERE e.entity_id IS NULL"""
        ).fetchone()[0]
        self.assertEqual(invalid_entities, 0)

    def test_topic_evaluation_reviews_can_be_drilled_down(self) -> None:
        result = analytics_queries.topic_evaluation_samples(
            self.connection, verdict="wrong_signal", page_size=100
        )
        self.assertEqual(result["total"], 0)  # v2 review not started yet
        pending = analytics_queries.topic_evaluation_samples(
            self.connection, verdict="pending", page_size=100
        )
        self.assertEqual(pending["total"], 300)
        self.assertTrue(all(item["evidence"] for item in pending["items"]))

    def test_papers_entity_ids_matches_papers_filter(self) -> None:
        result = queries.papers_entity_ids(self.connection, venue="CVPR", year=2025)
        self.assertGreater(result["total"], 0)
        self.assertEqual(len(result["entity_ids"]), result["total"])
        direct = self.connection.execute(
            """SELECT COUNT(DISTINCT m.entity_id)
               FROM paper_records p
               JOIN entity_memberships m ON m.record_id = p.record_id
               WHERE p.venue = 'CVPR' AND p.year = 2025"""
        ).fetchone()[0]
        self.assertEqual(result["total"], direct)
        record_count = self.connection.execute(
            "SELECT COUNT(*) FROM paper_records WHERE venue = 'CVPR' AND year = 2025"
        ).fetchone()[0]
        self.assertLessEqual(result["total"], record_count)

    def test_venue_overview_shape_and_invariants(self) -> None:
        """Data-insights home venue section: 30 venues, with scale/composition counts closing as integers."""
        result = analytics_queries.venue_overview(self.connection)
        self.assertEqual(result["total"], 30)
        venues = {item["venue"]: item for item in result["items"]}
        self.assertEqual(
            sum(item["record_count"] for item in result["items"]), 122_571
        )
        types = {item["venue_type"] for item in result["items"]}
        self.assertTrue(types == {"conference", "journal"})
        for item in result["items"]:
            # record count >= distinct entity count (an entity can have multiple records in the same venue)
            self.assertGreaterEqual(item["record_count"], item["entity_count"])
            self.assertGreater(item["entity_count"], 0)
            self.assertGreaterEqual(item["year_count"], 1)
            self.assertEqual(item["final_count"] + item["rolling_count"], item["record_count"])
            # mix integer closure + share closure + descending order
            buckets = item["topic_mix"]
            self.assertEqual(sum(bucket["entity_count"] for bucket in buckets), item["entity_count"])
            share_sum = sum(bucket["share_rate"] for bucket in buckets)
            self.assertGreaterEqual(share_sum, 99.5)
            self.assertLessEqual(share_sum, 100.5)
            counts = [bucket["entity_count"] for bucket in buckets]
            self.assertEqual(counts, sorted(counts, reverse=True))
            # the unclassified bucket may exist; buckets with a primary must carry a real family id
            for bucket in buckets:
                if bucket["name"] != "未分类":
                    self.assertTrue(bucket["topic_id"].startswith("f"))
        self.assertIn("ICML", venues)
        self.assertEqual(venues["ICML"]["min_year"], 2023)

    def test_venue_topic_mix_is_family_consistent(self) -> None:
        """The venue's family composition uses the same basis as topic_overview (rolled up to the family root) and does not exceed the whole-library family counts."""
        overview = analytics_queries.topic_overview(self.connection)
        family_counts = {item["topic_id"]: item["entity_count"] for item in overview["topics"]}
        result = analytics_queries.venue_overview(self.connection)
        for item in result["items"]:
            for bucket in item["topic_mix"]:
                if bucket["name"] == "未分类":
                    continue
                self.assertLessEqual(bucket["entity_count"], family_counts[bucket["topic_id"]])
        # spot-check a venue: the number of families listed in the mix does not exceed 10 + unclassified
        for item in result["items"]:
            self.assertLessEqual(len(item["topic_mix"]), 11)

    def test_topic_papers_entity_ids_matches_drilldown(self) -> None:
        result = analytics_queries.topic_papers_entity_ids(
            self.connection, topic_id="f3_agent_systems", year=2026
        )
        self.assertEqual(result["total"], 1_007)
        self.assertEqual(len(result["entity_ids"]), result["total"])


if __name__ == "__main__":
    unittest.main()
