from __future__ import annotations

import sqlite3
import socket
import time
import unittest
from unittest import mock
from pathlib import Path

from backend.library import planner, sources


CATALOG = Path(__file__).resolve().parents[2] / "data" / "database" / "catalog.sqlite"


def build_catalog_fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper_entities (
            entity_id TEXT PRIMARY KEY, canonical_record_id TEXT, canonical_title TEXT
        );
        CREATE TABLE paper_records (
            record_id TEXT PRIMARY KEY, authors TEXT, paper_url TEXT, doi TEXT,
            arxiv_id TEXT, pdf_url TEXT, venue TEXT, year INTEGER
        );
        CREATE TABLE entity_memberships (entity_id TEXT, record_id TEXT);
        """
    )
    records = [
        # record_id, authors, paper_url, doi, arxiv_id, pdf_url, venue, year
        ("r1", "Alice", "https://a", "10.1/a", "2401.00001v2", "", "CVPR", 2024),
        ("r2", "Bob", "https://b", "10.1/b", "", "https://openaccess.thecvf.com/content/CVPR2024/papers/x.pdf", "CVPR", 2024),
        ("r3", "Carol", "https://c", "10.1/c", "", "https://ieeexplore.ieee.org/document/123", "TPAMI", 2023),
        ("r4", "Dave", "https://d", "10.1/d", "", "https://example.com/paper.pdf", "ICML", 2025),
        ("r5", "Eve", "https://e", "10.1/e", "", "", "AAAI", 2023),
        ("r6", "Frank", "https://f", "10.1/f", "2402.00002", "https://ieeexplore.ieee.org/document/456", "TOG", 2023),
    ]
    connection.executemany("INSERT INTO paper_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)
    entities = [
        ("e1", "r1", "Paper One"),
        ("e2", "r2", "Paper Two"),
        ("e3", "r3", "Paper Three"),
        ("e4", "r4", "Paper Four"),
        ("e5", "r5", "Paper Five"),
        ("e6", "r6", "Paper Six"),
    ]
    connection.executemany("INSERT INTO paper_entities VALUES (?, ?, ?)", entities)
    memberships = [("e1", "r1"), ("e2", "r2"), ("e3", "r3"), ("e4", "r4"), ("e5", "r5"), ("e6", "r6")]
    connection.executemany("INSERT INTO entity_memberships VALUES (?, ?)", memberships)
    return connection


class PlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog_fixture()

    def tearDown(self) -> None:
        self.catalog.close()

    def test_source_classification_and_priority(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e2", "e3", "e4", "e5", "e6"])
        by_id = {item["entity_id"]: item for item in plan["items"]}
        self.assertEqual(by_id["e1"]["source"], "arxiv")
        self.assertEqual(by_id["e1"]["download_url"], "https://arxiv.org/pdf/2401.00001")
        self.assertEqual(by_id["e2"]["source"], "catalog")
        self.assertEqual(by_id["e3"]["source"], "none")
        self.assertEqual(by_id["e4"]["source"], "none")
        self.assertEqual(by_id["e5"]["source"], "none")
        # arxiv_id takes priority over the restricted pdf_url
        self.assertEqual(by_id["e6"]["source"], "arxiv")
        self.assertEqual(by_id["e6"]["download_url"], "https://arxiv.org/pdf/2402.00002")

    def test_summary_counts_and_size_estimate(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e2", "e3", "e4", "e5", "e6"])
        summary = plan["summary"]
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["counts"], {
            "arxiv": 2, "catalog": 1, "none": 3,
        })
        self.assertEqual(summary["downloadable"], 3)
        self.assertEqual(
            summary["estimated_total_bytes"],
            2 * planner.ARXIV_ESTIMATED_BYTES + 1 * planner.OPEN_ESTIMATED_BYTES,
        )

    def test_downloadable_items_have_url(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e2", "e3", "e4", "e5", "e6"])
        for item in plan["items"]:
            if item["downloadable"]:
                self.assertTrue(item["download_url"])
                self.assertGreater(item["estimated_bytes"], 0)
            else:
                self.assertEqual(item["download_url"], "")
                self.assertEqual(item["estimated_bytes"], 0)

    def test_input_dedup(self) -> None:
        plan = planner.build_plan(self.catalog, ["e1", "e1", "e2"])
        self.assertEqual(plan["summary"]["total"], 3)
        duplicates = [item for item in plan["items"] if item.get("reason_code") == "duplicate_entity"]
        self.assertEqual(len(duplicates), 1)
        self.assertFalse(duplicates[0]["downloadable"])

    def test_empty_input(self) -> None:
        plan = planner.build_plan(self.catalog, [])
        self.assertEqual(plan["summary"]["total"], 0)
        self.assertEqual(plan["items"], [])

    def test_source_fallback_and_ambiguous_candidates_are_explicit(self) -> None:
        arxiv = lambda _: [{"title": "Paper Five", "year": 2023, "authors": ["Eve"], "pdf_url": "https://arxiv.org/pdf/1"}]
        plan = planner.build_plan(self.catalog, ["e5"], arxiv_lookup=arxiv)
        self.assertEqual(plan["items"][0]["source"], "arxiv")
        self.assertEqual(plan["items"][0]["match_basis"], "title_author_or_year")
        ambiguous = planner.build_plan(self.catalog, ["e5"], arxiv_lookup=lambda _: [
            {"title": "Paper Five", "year": 2023, "authors": ["Eve"], "pdf_url": "https://arxiv.org/pdf/1"},
            {"title": "Paper Five", "year": 2023, "authors": ["Eve"], "pdf_url": "https://arxiv.org/pdf/2"},
        ])
        self.assertFalse(ambiguous["items"][0]["downloadable"])
        self.assertEqual(ambiguous["items"][0]["reason_code"], "ambiguous_match")

        oa = planner.build_plan(self.catalog, ["e5"], openalex_lookup=lambda _: [{
            "title": "Paper Five", "year": 2023, "authors": ["Eve"],
            "pdf_url": "https://openreview.net/pdf?id=paper-five",
        }])
        self.assertEqual(oa["items"][0]["source"], "openalex")
        self.assertTrue(oa["items"][0]["downloadable"])

    def test_catalog_candidate_does_not_preflight_later_sources(self) -> None:
        plan = planner.build_plan(
            self.catalog, ["e2"],
            openalex_lookup=lambda _: [{"title": "Paper Two", "year": 2024, "authors": ["Bob"],
                                        "pdf_url": "https://openreview.net/pdf?id=paper-two"}],
        )
        item = plan["items"][0]
        self.assertEqual(item["source"], "catalog")
        self.assertEqual(item["fallback_sources"], [])

    def test_network_failure_is_not_cached_as_no_source(self) -> None:
        sources._CANDIDATE_CACHE.clear()
        item = {"title": "Paper Five", "doi": "", "arxiv_id": ""}
        with mock.patch("backend.library.sources.urllib.request.urlopen") as request:
            request.side_effect = OSError("offline")
            self.assertEqual(sources.remote_openalex_candidates(item), [])
            self.assertEqual(sources.remote_openalex_candidates(item), [])
        self.assertEqual(request.call_count, 2)

    def test_definitive_empty_lookup_is_negative_cached(self) -> None:
        sources._CANDIDATE_CACHE.clear()
        item = {"title": "Paper Five", "doi": "", "arxiv_id": ""}
        loader = mock.Mock(return_value=sources.LookupCandidates([], attempts=[{
            "source": "openalex", "status": "ok", "candidate_count": 0, "direct_pdf_count": 0,
        }]))
        self.assertEqual(sources._cached("openalex", item, loader), [])
        self.assertEqual(sources._cached("openalex", item, loader), [])
        self.assertEqual(loader.call_count, 1)

    def test_source_timeout_is_visible_and_not_negative_cached(self) -> None:
        sources._CANDIDATE_CACHE.clear()
        item = {"title": "Paper Five", "doi": "", "arxiv_id": ""}
        with mock.patch("backend.library.sources.urllib.request.urlopen") as request:
            request.side_effect = socket.timeout("timed out")
            resolved = sources.resolve(item, openalex_lookup=sources.remote_openalex_candidates)
            self.assertEqual(resolved["reason_code"], "source_timeout")
            self.assertEqual(resolved["source_attempts"][0]["source"], "OpenAlex")
            self.assertIn("超时", resolved["message"])
            sources.remote_openalex_candidates(item)
        self.assertEqual(request.call_count, 2)

    def test_definitive_no_source_remains_distinct_from_lookup_failure(self) -> None:
        resolved = sources.resolve(
            {"title": "Paper Five", "doi": "", "arxiv_id": "", "authors": "Eve", "years": [2023]},
            arxiv_lookup=lambda _: sources.LookupCandidates([], attempts=[{"source": "arxiv", "status": "ok", "candidate_count": 0}]),
            openalex_lookup=lambda _: sources.LookupCandidates([], attempts=[{"source": "openalex", "status": "ok", "candidate_count": 0, "direct_pdf_count": 0}]),
        )
        self.assertEqual(resolved["reason_code"], "no_download_source")
        self.assertEqual(len(resolved["source_attempts"]), 2)

    def test_local_plan_never_calls_external_lookup_and_is_fast(self) -> None:
        with mock.patch("backend.library.sources.remote_arxiv_candidates", side_effect=AssertionError("network")), \
             mock.patch("backend.library.sources.remote_openalex_candidates", side_effect=AssertionError("network")):
            started = time.monotonic()
            plan = planner.build_plan(self.catalog, ["e5"])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(plan["items"][0]["source"], "none")


class RealCatalogPlannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CATALOG.exists():
            raise unittest.SkipTest("catalog.sqlite 尚未构建")

    def test_plan_against_real_catalog(self) -> None:
        catalog = sqlite3.connect(CATALOG)
        catalog.row_factory = sqlite3.Row
        try:
            merged = catalog.execute(
                "SELECT entity_id FROM paper_entities WHERE entity_status = 'merged' ORDER BY entity_id LIMIT 20"
            ).fetchall()
            self.assertTrue(merged)
            ids = [row["entity_id"] for row in merged]
            plan = planner.build_plan(catalog, ids)
            self.assertEqual(plan["summary"]["total"], len(ids))
            self.assertTrue(all(item["title"] for item in plan["items"]))
            self.assertEqual(
                sum(plan["summary"]["counts"].values()), len(ids)
            )
            self.assertEqual(
                sum(1 for item in plan["items"] if item["downloadable"]),
                plan["summary"]["downloadable"],
            )
        finally:
            catalog.close()


if __name__ == "__main__":
    unittest.main()
